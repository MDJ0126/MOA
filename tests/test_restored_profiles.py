import json
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from app import Store
from app_lifecycle import request_stop
from data_backup import backup,restore
from environment_profiles import DEFAULT,settings,recommend
from soak import freeze


class RestoredProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'original')
        self.tid=self.store.add('preserve frozen task','demo')
        with self.store.connect() as c:self.frozen=c.execute('SELECT execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()[0]

    def restored(self,raw,index):
        with self.store.connect() as c:c.execute("INSERT OR REPLACE INTO settings VALUES('execution_profile',?)",(raw,))
        archive=self.root/f'{index}.zip';backup(self.store.root,archive)
        destination=self.root/f'restored-{index}';restore(archive,destination)
        return Store(destination)

    def test_invalid_profiles_are_archived_and_restore_to_usable_defaults(self):
        for index,raw in enumerate(('{broken','[]','null',b'\xff',None,'{"budget_percent":true}','{"updated":-1}','{"updated":NaN}')):
            with self.subTest(raw=repr(raw)):
                restored=self.restored(raw,index);profile=settings(restored)
                self.assertEqual(profile,{**DEFAULT,'updated':0})
                result=recommend(profile,{'os':'Windows','architecture':'AMD64','ram_gib':16,'available_ram_gib':10,'logical_cpus':8,'cpu_used_percent':0})
                self.assertEqual(result['target_os'],'windows');self.assertFalse(result['connected'])
                with restored.connect() as c:self.assertEqual(c.execute("SELECT value FROM settings_recovery_archive WHERE key='execution_profile'").fetchone()[0],raw)
                with restored.connect() as c:self.assertEqual(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],self.frozen)
                with self.store.connect() as c:self.assertEqual(c.execute("SELECT value FROM settings WHERE key='execution_profile'").fetchone()[0],raw)

    def test_valid_partial_profile_is_preserved_without_connecting(self):
        raw=json.dumps({'target_os':'linux','location':'remote','updated':12})
        restored=self.restored(raw,'valid')
        self.assertEqual(settings(restored),{'target_os':'linux','location':'remote','budget_percent':50,'updated':12})
        self.assertIsNone(recommend(settings(restored),{})['recommended'])

    def test_archive_failure_never_publishes_restored_data(self):
        with self.store.connect() as c:
            c.execute('CREATE TABLE settings_recovery_archive(id INTEGER PRIMARY KEY,key TEXT NOT NULL,value,token TEXT NOT NULL,created REAL NOT NULL)')
            c.execute("CREATE TRIGGER reject_profile_archive BEFORE INSERT ON settings_recovery_archive WHEN NEW.key='execution_profile' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.restored('{broken','blocked')
        self.assertFalse((self.root/'restored-blocked').exists())

    def test_restored_profile_can_be_loaded_and_resaved_through_http(self):
        restored=self.restored('{broken','http');project=self.root/'source';freeze(project)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base='http://127.0.0.1:'+str(port)
        with (self.root/'server.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,str(project/'src/app.py'),'--demo','--data',str(restored.root),'--port',str(port)],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            def request(path,payload=None):
                req=urllib.request.Request(base+path,None if payload is None else json.dumps(payload).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
                with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)
            try:
                for _ in range(100):
                    try:
                        self.assertEqual(request('/api/health')['app']['pid'],process.pid);break
                    except (OSError,urllib.error.URLError):
                        if process.poll() is not None:self.fail('isolated restored app exited')
                        time.sleep(.05)
                else:self.fail('isolated restored app readiness timeout')
                setup=request('/api/setup')
                self.assertEqual(setup['execution_profile'],{**DEFAULT,'updated':0})
                self.assertFalse(setup['environment_recommendation']['connected'])
                saved=request('/api/setup/execution-profile',{'target_os':'auto','location':'host','budget_percent':25,'expected_updated':0})
                self.assertEqual(saved['location'],'host');self.assertGreater(saved['updated'],0)
                with self.assertRaises(urllib.error.HTTPError) as stale:
                    request('/api/setup/execution-profile',{'target_os':'linux','location':'remote','budget_percent':75,'expected_updated':0})
                try:self.assertEqual(stale.exception.code,409)
                finally:stale.exception.close()
                self.assertEqual(request('/api/setup')['execution_profile'],saved)
                self.assertTrue(request('/api/state')['paused'])
                with restored.connect() as c:
                    self.assertEqual(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],self.frozen)
                    self.assertEqual(c.execute("SELECT value FROM settings_recovery_archive WHERE key='execution_profile'").fetchone()[0],'{broken')
            finally:
                if process.poll() is None:
                    try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=10)
                    finally:
                        if process.poll() is None:process.terminate();process.wait(timeout=5)


if __name__=='__main__':unittest.main()
