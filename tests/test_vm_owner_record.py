import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import tempfile
import unittest
from app import Store
from setup_config import write_setting
from vm_runtime import VM
from soak import freeze
from app_lifecycle import request_stop


class _VMOwnerFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.vm=VM(self.store)
    def raw(self):
        with self.store.connect() as connection:return connection.execute("SELECT value FROM settings WHERE key='vm_owner'").fetchone()[0]

class VMOwnerRecordTests(_VMOwnerFixture):
    def test_corrupt_owner_is_unknown_without_mutating_raw_record(self):
        for raw in ('{private','[]','{}','false','1',b'\xff'):
            with self.subTest(raw=repr(raw)):
                with self.store.connect() as connection:connection.execute("INSERT OR REPLACE INTO settings VALUES('vm_owner',?)",(raw,))
                result=self.vm.status()
                self.assertIsNone(result['owner']);self.assertTrue(result['owner_unavailable']);self.assertEqual(result['state'],'unreachable');self.assertTrue(result['error'])
                self.assertEqual(self.raw(),raw);json.dumps(result,allow_nan=False)
        self.assertFalse((self.vm.folder/'desktop.lock').exists())
    def test_active_owner_only_exposes_validated_fields(self):
        owner={'task_id':'fixture','agent':'builder','role':'developer','since':123,'private_note':'do not expose','unused':float('nan')};write_setting(self.store,'vm_owner',owner);original=self.raw()
        with self.vm.exclusive():result=self.vm.status()
        self.assertEqual(result['owner'],{key:owner[key] for key in ('task_id','agent','role','since')});self.assertFalse(result['owner_unavailable'])
        json.dumps(result,allow_nan=False);self.assertEqual(self.raw(),original)
    def test_invalid_owner_time_or_identity_is_not_reported_as_idle(self):
        base={'task_id':'fixture','agent':'builder','role':'developer','since':123}
        for changes in ({'since':float('nan')},{'since':True},{'agent':None},{'task_id':''},{'role':'unknown'}):
            with self.subTest(changes=changes):
                write_setting(self.store,'vm_owner',{**base,**changes});original=self.raw()
                result=self.vm.status();self.assertEqual(result['state'],'unreachable');self.assertTrue(result['owner_unavailable']);self.assertEqual(self.raw(),original)


class VMOwnerHttpTests(_VMOwnerFixture):
    def test_corrupt_owner_does_not_block_http_or_local_demo_work(self):
        raw='{private owner record'
        with self.store.connect() as connection:connection.execute("INSERT OR REPLACE INTO settings VALUES('vm_owner',?)",(raw,))
        project=Path(self.temp.name)/'source';freeze(project)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        def request(path,payload=None):
            message=urllib.request.Request(f'http://127.0.0.1:{port}'+path,None if payload is None else json.dumps(payload).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
            with urllib.request.urlopen(message,timeout=3) as response:return json.load(response,parse_constant=lambda value:self.fail('non-finite response'))
        with (Path(self.temp.name)/'server.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,str(project/'app.py'),'--data',str(self.store.root),'--port',str(port)],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for _ in range(100):
                    try:request('/api/health');break
                    except OSError:
                        if process.poll() is not None:self.fail('isolated server exited')
                        time.sleep(.05)
                else:self.fail('isolated server startup timeout')
                for state in (request('/api/vm'),request('/api/state')['vm']):
                    self.assertTrue(state['owner_unavailable']);self.assertEqual(state['state'],'unreachable');self.assertIsNone(state['owner'])
                self.assertEqual(self.raw(),raw)
                task=request('/api/tasks',{'title':'local demo remains usable','mode':'demo'})
                for _ in range(100):
                    state=request('/api/state');row=next(item for item in state['tasks'] if item['id']==task['id'])
                    if row['status']=='completed':break
                    time.sleep(.05)
                else:self.fail('isolated local demo did not complete')
                self.assertTrue(state['vm']['owner_unavailable']);self.assertEqual(self.raw(),raw)
                self.assertEqual(request('/api/health')['app']['pid'],process.pid)
                self.assertFalse((self.vm.folder/'desktop.lock').exists())
            finally:
                if process.poll() is None:
                    try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=10)
                    except Exception:process.terminate();process.wait(timeout=10)
