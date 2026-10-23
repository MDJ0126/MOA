import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
import urllib.request
from app import Store
from app_lifecycle import request_stop
from verify_clean_install import copy_sources


class EnvironmentRecommendationHttpTests(unittest.TestCase):
    def test_preview_and_auto_preparation_respect_unknown_and_unsupported_hosts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root=Path(temporary);project=root/'source';copy_sources(project);data=root/'data';store=Store(data);store.control('pause')
            (project/'config.json').write_text(json.dumps({'default_mode':'demo','model':'','model_url':'http://127.0.0.1:0','slack':{'enabled':False}}))
            (project/'install.iso').write_bytes(b'fixture only; never booted')
            host_file=root/'host.json';normal={'os':'Windows','architecture':'AMD64','logical_cpus':12,'ram_gib':16,'available_ram_gib':10,'cpu_used_percent':0};host_file.write_text(json.dumps(normal))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            base='http://127.0.0.1:'+str(port)
            code="import json,sys;from pathlib import Path;import app,environment_profiles,vbox_setup;environment_profiles.hardware=lambda:json.loads(Path(sys.argv[1]).read_text());app.hardware=environment_profiles.hardware;vbox_setup.executable=lambda:'fixture';vbox_setup.command=lambda *a:(_ for _ in ()).throw(AssertionError('VM command forbidden'));app.serve(Path(sys.argv[2]),int(sys.argv[3]),'','http://127.0.0.1:0')"
            def request(path,value=None):
                req=urllib.request.Request(base+path,None if value is None else json.dumps(value).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
                with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)
            with (root/'server.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,'-c',code,str(host_file),str(data),str(port)],cwd=project,stdout=log,stderr=log)
                try:
                    for _ in range(100):
                        try:request('/api/health');break
                        except OSError:
                            if process.poll() is not None:self.fail('isolated app exited')
                            time.sleep(.05)
                    else:self.fail('isolated app readiness timeout')
                    initial=request('/api/setup')['execution_profile']
                    good=request('/api/setup/execution-preview',{})
                    self.assertEqual(good['recommended'],'development');self.assertTrue(good['preparation_supported']);self.assertFalse(good['connected'])
                    for changes,profile in (({'architecture':'arm64'},{}),({}, {'target_os':'macos'}),({'cpu_used_percent':None},{}),({'available_ram_gib':1},{})):
                        with self.subTest(changes=changes,profile=profile):
                            host_file.write_text(json.dumps({**normal,**changes}))
                            result=request('/api/setup/execution-preview',profile)
                            self.assertIsNone(result['recommended']);self.assertTrue(all(p['fits_now'] is not True for p in result['presets']))
                            if changes.get('architecture')!='arm64' and profile.get('target_os')!='macos':
                                with self.assertRaises(HTTPError) as rejected:request('/api/vbox/plan',{'profile':{'target_os':'windows','location':'shared_vm'},'preset':'auto','iso':'install.iso'})
                                self.assertEqual(rejected.exception.code,400);rejected.exception.close()
                    self.assertEqual(request('/api/setup')['execution_profile'],initial)
                    self.assertEqual(request('/api/health')['app']['pid'],process.pid)
                    self.assertEqual(request('/api/vbox/setup')['plan'],None)
                finally:
                    if process.poll() is None:
                        try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=10)
                        except Exception:process.terminate();process.wait(timeout=10)
