from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from app import Store,Worker,serve
from setup_config import PRESETS,save_team,write_setting
from verify_clean_install import copy_sources


class DemoLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name);self.project=self.root/'source';copy_sources(self.project)
    def tearDown(self):self.temp.cleanup()

    @contextmanager
    def launch(self,powershell=False):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        command=[sys.executable,str(self.project/'app.py'),'--demo','--port',str(port)]
        if powershell:
            shell=shutil.which('pwsh') or shutil.which('powershell')
            if not shell:self.skipTest('PowerShell unavailable')
            command=[shell,'-NoProfile','-NonInteractive','-File',str(self.project/'Start.ps1'),'-Demo','-Port',str(port)]
        with (self.root/'server.log').open('wb') as log:
            process=subprocess.Popen(command,cwd=self.root,stdout=log,stderr=log,env=dict(os.environ,MOA_PYTHON=sys.executable),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            base=f'http://127.0.0.1:{port}'
            def request(path,body=None):
                req=urllib.request.Request(base+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
                with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)
            request.port=port
            try:
                for _ in range(120):
                    try:request('/api/health');break
                    except OSError:
                        if process.poll() is not None:self.fail((self.root/'server.log').read_text(errors='replace'))
                        time.sleep(.05)
                else:self.fail('demo readiness timeout')
                yield request
            finally:
                try:request('/api/control',{'action':'shutdown'})
                except OSError:pass
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)

    def test_powershell_demo_starts_without_installed_artifacts(self):
        with self.launch(powershell=True) as request:
            state=request('/api/state');self.assertTrue(state['demo_only']);self.assertEqual(state['default_mode'],'demo');self.assertEqual(state['model_url'],'http://127.0.0.1:0')
            with urllib.request.urlopen(f'http://127.0.0.1:{request.port}/ui.js',timeout=3) as response:self.assertIn(b'applyDemoProfile',response.read())
            tid=request('/api/tasks',{'title':'first demo'})['id']
            for _ in range(100):
                tasks=request('/api/state')['tasks']
                if tasks and tasks[0]['status']=='completed':break
                time.sleep(.05)
            else:self.fail('demo task did not complete')
            report=json.loads((self.project/'data-demo'/'tasks'/tid/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['mode'],'demo');self.assertEqual(report['verification']['tests_run'],0)
            for name in ('models','runtime','data'):self.assertFalse((self.project/name).exists(),name)
            self.assertFalse((self.project/'data-demo'/'supervisor.json').exists())

    def test_demo_api_rejects_actual_model_vm_and_slack(self):
        with self.launch() as request:
            for path,body in [('/api/tasks',{'title':'real','mode':'llama'}),('/api/tasks',{'title':'VM','environment':'vm'}),('/api/setup/slack',{}),('/api/slack/test',{}),('/api/setup/vm',{}),('/api/vm/control',{'action':'start'}),('/api/vm/control',{'action':'stop'}),('/api/vm/input',{'action':'text','text':'blocked'}),('/api/vm/screen',None)]:
                with self.subTest(path=path,body=body),self.assertRaises(urllib.error.HTTPError) as caught:request(path,body)
                self.assertEqual(caught.exception.code,400);caught.exception.close()
            self.assertEqual(request('/api/state')['tasks'],[])
            self.assertEqual(request('/api/vm')['state'],'stopped')

    def test_operating_data_and_connected_data_are_rejected_before_start(self):
        with patch('app.ROOT',self.project),patch('app.Worker') as worker:
            with self.assertRaisesRegex(ValueError,'운영 data'):serve(self.project/'data',0,'','',demo_only=True)
            self.assertFalse((self.project/'data').exists())
            store=Store(self.root/'connected');write_setting(store,'slack',{'enabled':True})
            with self.assertRaisesRegex(ValueError,'연결 설정'):serve(store.root,0,'','',demo_only=True)
            worker.assert_not_called()

    def test_worker_blocks_injected_non_demo_job_without_backend_or_vm(self):
        store=Store(self.root/'jobs');save_team(store,PRESETS[0]);tid=store.add('injected','llama')
        vm_tid=store.add('injected VM','demo')
        with store.connect() as c:
            plan=json.loads(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(vm_tid,)).fetchone()[0]);plan['environment']['kind']='vm'
            c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',(json.dumps(plan),vm_tid))
        worker=Worker(store,demo_only=True)
        with patch('app.Llama') as model,patch.object(worker.vm,'stage') as vm:
            worker.tick();worker.tick();model.assert_not_called();vm.assert_not_called()
        self.assertTrue(all(task['status']=='interrupted' for task in store.snapshot()['tasks']))
        self.assertFalse((store.root/'tasks'/tid).exists())
        self.assertFalse((store.root/'tasks'/vm_tid).exists())
