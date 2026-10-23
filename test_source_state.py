from types import SimpleNamespace
from unittest.mock import patch
import os
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from app import Store
from app_lifecycle import request_stop
from verify_clean_install import copy_sources


class RunningSourceHttpTests(unittest.TestCase):
    def test_changed_dependency_requires_restart_without_stopping_app(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root=Path(temporary);project=root/'source';copy_sources(project);store=Store(root/'data');store.control('pause')
            (project/'config.json').write_text(json.dumps({'default_mode':'demo','model':'','model_url':'http://127.0.0.1:0','slack':{'enabled':False}}))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            base='http://127.0.0.1:'+str(port)
            def request(path):
                with urllib.request.urlopen(base+path,timeout=3) as response:return json.load(response)
            with (root/'app.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,str(project/'app.py'),'--data',str(store.root),'--port',str(port)],stdout=log,stderr=log)
                try:
                    for _ in range(100):
                        try:health=request('/api/health');break
                        except OSError:
                            if process.poll() is not None:self.fail('isolated app exited')
                            time.sleep(.05)
                    else:self.fail('isolated app readiness timeout')
                    source=project/'environment_profiles.py';original=source.read_bytes();source.write_bytes(original+b'\n# pending source update\n')
                    result=request('/api/diagnostics')
                    check=next((c for c in result['checks'] if c['id']=='application_sources'),None)
                    self.assertIsNotNone(check);self.assertEqual(check['state'],'restart')
                    self.assertEqual(result['source_status']['state'],'changed');self.assertIn('environment_profiles.py',result['source_status']['changed_files'])
                    self.assertEqual(result['app_source_sha256'],health['app_source_sha256'])
                    self.assertEqual(request('/api/health')['app']['pid'],process.pid)
                    source.write_bytes(original)
                    self.assertEqual(request('/api/diagnostics')['source_status']['state'],'matching')
                    (project/'ui.css').write_text('/* frontend update only */')
                    self.assertEqual(request('/api/diagnostics')['source_status']['state'],'matching')
                    source.unlink()
                    self.assertEqual(request('/api/diagnostics')['source_status']['state'],'unknown')
                    source.write_bytes(original)
                finally:
                    if process.poll() is None:
                        try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=10)
                        except Exception:process.terminate();process.wait(timeout=10)


class SourceStateUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name)
        (self.root/'backend.py').write_text('value=1\n')
    def tearDown(self):self.temp.cleanup()
    def state(self,**kwargs):
        from source_state import SourceState
        return SourceState(self.root,['backend.py'],**kwargs)

    def test_config_creation_value_change_and_format_only_change(self):
        state=self.state();self.assertEqual(state.inspect()['state'],'matching')
        config=self.root/'config.json';config.write_text('{"model":"one"}')
        self.assertEqual(state.inspect(force=True)['state'],'changed')
        configured=self.state();config.write_text(' { "model" : "one" } ')
        self.assertEqual(configured.inspect(force=True)['state'],'matching')
        config.write_text('{"model":"two"}')
        self.assertEqual(configured.inspect(force=True)['changed_files'],['config.json'])

    def test_malformed_config_is_unknown_instead_of_restart_advice(self):
        state=self.state();(self.root/'config.json').write_text('{broken')
        result=state.inspect(force=True);self.assertEqual(result['state'],'unknown');self.assertIsNone(result['restart_required'])

    def test_unreadable_or_missing_source_is_not_reported_matching(self):
        state=self.state()
        with patch('source_state.Path.open',side_effect=PermissionError('denied')):self.assertEqual(state.inspect(force=True)['state'],'unknown')
        (self.root/'backend.py').unlink();self.assertEqual(state.inspect(force=True)['state'],'unknown')

    def test_shared_source_is_not_hashed_as_a_trusted_version(self):
        state=self.state();os.link(self.root/'backend.py',self.root/'linked.py')
        self.assertEqual(state.inspect(force=True)['state'],'unknown')

    def test_cache_is_bounded_and_does_not_change_startup_baseline(self):
        clock=[0];state=self.state(clock=lambda:clock[0]);first=state.inspect();first['changed_files'].append('fake')
        (self.root/'backend.py').write_text('value=2\n')
        self.assertEqual(state.inspect()['state'],'matching');self.assertEqual(state.inspect()['changed_files'],[])
        clock[0]=6;self.assertEqual(state.inspect()['state'],'changed')
        self.assertTrue(state.inspect(force=True)['restart_required'])

    def test_only_loaded_project_python_sources_are_selected(self):
        from source_state import SourceState
        fixtures={'fixture_backend':SimpleNamespace(__file__=str(self.root/'backend.py')),'fixture_test':SimpleNamespace(__file__=str(self.root/'test_debug.py')),'fixture_external':SimpleNamespace(__file__=str(self.root.parent/'external.py'))}
        with patch.dict('source_state.sys.modules',fixtures):state=SourceState(self.root)
        self.assertEqual(state.files,['backend.py','config.json'])
