"""Probe HTTP response and persistence; guest transport is mocked, no real VM."""
import json,socket,subprocess,sys,tempfile,time,unittest,urllib.request
from pathlib import Path
from app import Store
from app_lifecycle import request_stop
from setup_config import read_setting
from verify_clean_install import copy_sources

class WindowsProbeHttpTests(unittest.TestCase):
 def test_probe_returns_response_and_persists_verified_session(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
   root=Path(temporary);project=root/'source';copy_sources(project);data=root/'data';store=Store(data);store.control('pause')
   (project/'config.json').write_text(json.dumps({'default_mode':'demo','model':'','slack':{'enabled':False}}))
   with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   code="import app,windows_guest,sys;from pathlib import Path;app.VM.session=lambda self:{'name':'probe-fixture','config':{'guest_os':'windows'}};app.VM.qmp=lambda *a,**k:{'status':'running'};windows_guest.rpc=lambda *a,**k:{'id':'mswindows','name':'Microsoft Windows'};app.serve(Path(sys.argv[1]),int(sys.argv[2]),'','http://127.0.0.1:0')"
   def request(path,value=None):
    req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,None if value is None else json.dumps(value).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
   with (root/'server.log').open('wb') as log:
    process=subprocess.Popen([sys.executable,'-c',code,str(data),str(port)],cwd=project,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
     for _ in range(100):
      try:request('/api/health');break
      except OSError:
       if process.poll() is not None:self.fail('isolated server exited')
       time.sleep(.05)
     else:self.fail('isolated server readiness timeout')
     value=request('/api/vm/windows-probe',{})
     self.assertEqual(value['session'],'probe-fixture');self.assertEqual(value['os']['id'],'mswindows')
     self.assertEqual(read_setting(store,'windows_guest_probe',{}),value)
    finally:
     if process.poll() is None:
      try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=12)
      except Exception:process.terminate();process.wait(timeout=5)
