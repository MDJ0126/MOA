import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from app import Store,serve
import supervisor

class StartupOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_port_conflict_does_not_recover_tasks_or_start_slack(self):
        data=self.root/'data';store=Store(data);tid=store.add('leave running task untouched','demo')
        with store.connect() as c:c.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen();port=occupied.getsockname()[1]
            with patch('app.SlackService.loop') as slack_loop:
                with self.assertRaises(OSError):serve(data,port,'','http://127.0.0.1:0')
                slack_loop.assert_not_called()
        with store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'running')
    def test_supervisor_shutdown_never_posts_to_a_port_without_child_ownership(self):
        (self.root/'runtime.exe').write_bytes(b'fixture');(self.root/'model.gguf').write_bytes(b'fixture')
        config={'runtime_executable':'runtime.exe','model_file':'model.gguf','context_size':32,'threads':1,'model':'fixture','model_url':'http://127.0.0.1:8766'}
        (self.root/'config.json').write_text(json.dumps(config));requests=[];root=self.root
        class Process:
            pid=123
            def poll(self):return None
            def wait(self,timeout=None):return 0
        class Child:
            def __init__(self,name,*args):self.name=name;self.process=Process();self.restarts=0
            def step(self):
                if self.name=='app':(root/'data/supervisor.stop').touch()
                return None
            def status(self):return {'running':True,'pid':123}
            def close(self):pass
        def foreign_response(request,**kwargs):requests.append(request.full_url);return io.BytesIO(b'{}')
        with patch.object(supervisor,'ROOT',self.root),patch.object(supervisor,'ManagedChild',Child),patch.object(supervisor,'check_ports',return_value=None,create=True),patch('supervisor.signal.signal'),patch('supervisor.time.sleep'),patch('urllib.request.urlopen',side_effect=foreign_response):supervisor.main()
        self.assertEqual(requests,[],'shutdown must not send control to a foreign service on the fixed port')

    def test_supervisor_preflight_failure_starts_nothing_and_releases_lock(self):
        (self.root/'config.json').write_text('{}')
        with patch.object(supervisor,'ROOT',self.root),patch.object(supervisor,'check_ports',side_effect=RuntimeError('occupied')),patch('supervisor.subprocess.Popen') as launch:
            with self.assertRaises(RuntimeError):supervisor.main()
            launch.assert_not_called()
        self.assertFalse((self.root/'data/model.key').exists())
        with supervisor.acquire(self.root/'data/supervisor.lock'):pass
    def test_native_port_probe_preserves_an_existing_listener(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen();port=occupied.getsockname()[1]
            with self.assertRaises(RuntimeError):supervisor.check_ports((port,))
            self.assertEqual(occupied.getsockname()[1],port)
    def test_request_is_bound_to_project_instance_and_expected_pid(self):
        from app_lifecycle import register,request_stop,stop_requested,unregister
        import os
        value=register(self.root,12345,'a'*32)
        with self.assertRaises(ValueError):request_stop(self.root,12345,expected_pid=os.getpid()+1)
        self.assertFalse(stop_requested(self.root,value))
        request_stop(self.root,12345,expected_pid=os.getpid());request_stop(self.root,12345)
        self.assertTrue(stop_requested(self.root,value));unregister(self.root,value)
        self.assertFalse((self.root/'runtime/app-12345.json').exists())
    def test_foreign_or_escaped_registry_is_rejected_and_cleanup_keeps_new_owner(self):
        from app_lifecycle import register,request_stop,unregister,read_registration
        value=register(self.root,12345,'a'*32);path=self.root/'runtime/app-12345.json'
        for change in ({'root':'different-project'},{'stop_file':'../outside'},{'pid':True},{'instance':'not-an-id'}):
            path.write_text(json.dumps({**value,**change}))
            with self.assertRaises(ValueError):request_stop(self.root,12345)
        self.assertFalse((self.root/'outside').exists())
        newer=register(self.root,12345,'b'*32);unregister(self.root,value)
        self.assertEqual(read_registration(self.root,12345)['instance'],newer['instance'])
    def test_native_demo_stops_through_powershell_without_http_control(self):
        import os,shutil,subprocess,sys,time,urllib.request
        from app_lifecycle import read_registration,request_stop
        from verify_clean_install import copy_sources
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if not shell:self.skipTest('PowerShell unavailable')
        project=self.root/'project';copy_sources(project)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with (self.root/'server.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,str(project/'app.py'),'--demo','--port',str(port)],stdout=log,stderr=log)
            try:
                base='http://127.0.0.1:'+str(port)
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base+'/api/health',timeout=1) as response:health=json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None:self.fail('isolated app exited')
                        time.sleep(.05)
                else:self.fail('isolated app readiness timeout')
                record=read_registration(project,port);self.assertEqual(record['pid'],process.pid)
                other=self.root/'other';other.mkdir()
                with self.assertRaises(ValueError):request_stop(other,port)
                with urllib.request.urlopen(base+'/api/health',timeout=2) as response:self.assertEqual(json.load(response)['app']['pid'],health['app']['pid'])
                result=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(project/'Stop.ps1'),'-Port',str(port)],env={**os.environ,'MOA_PYTHON':sys.executable},capture_output=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'));self.assertEqual(process.wait(timeout=10),0)
                self.assertFalse((project/'runtime'/record['stop_file']).exists());self.assertFalse((project/'runtime'/('app-'+str(port)+'.json')).exists())
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=10)

if __name__=='__main__':unittest.main()
