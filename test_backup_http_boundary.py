from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
import urllib.request
from app import Store
from app_lifecycle import request_stop
from backup_jobs import schedule_request
from verify_clean_install import copy_sources


class BackupHttpBoundaryTests(unittest.TestCase):
    def test_unsafe_backup_files_reject_request_and_replay_without_shutdown(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root=Path(temporary);project=root/'source';copy_sources(project)
            (project/'config.json').write_text(json.dumps({'default_mode':'demo','model':'','model_url':'http://127.0.0.1:0','slack':{'enabled':False}}))
            store=Store(root/'data');store.control('pause')
            receipt=schedule_request(store,project,'request-123',123,lambda *a,**k:SimpleNamespace(pid=456))
            archive=project/'backups'/receipt['file'];status=archive.with_suffix('.status.json')
            outside=root/'unrelated';outside.write_text(json.dumps({'state':'completed'}))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            base='http://127.0.0.1:'+str(port)
            def request(path,data=None):
                req=urllib.request.Request(base+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
                with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)
            with (root/'app.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,str(project/'app.py'),'--data',str(store.root),'--port',str(port)],stdout=log,stderr=log)
                try:
                    for _ in range(100):
                        try:request('/api/health');break
                        except OSError:
                            if process.poll() is not None:self.fail('isolated app exited')
                            time.sleep(.05)
                    else:self.fail('isolated app readiness timeout')
                    for kind in ('status-link','archive-link','oversize')+(('locked-status',) if os.name=='nt' else ()):
                        with self.subTest(kind=kind),ExitStack() as held:
                            archive.unlink(missing_ok=True);status.unlink(missing_ok=True)
                            if kind=='status-link':archive.write_bytes(b'archive');os.link(outside,status)
                            elif kind=='archive-link':os.link(outside,archive);status.write_text(json.dumps({'state':'completed'}))
                            elif kind=='oversize':archive.write_bytes(b'archive');status.write_text(' '*5000+json.dumps({'state':'completed'}))
                            else:
                                archive.write_bytes(b'archive');status.write_text(json.dumps({'state':'completed'}))
                                kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];kernel.CreateFileW.restype=ctypes.c_void_p;kernel.CloseHandle.argtypes=[ctypes.c_void_p]
                                handle=kernel.CreateFileW(str(status),0xC0000000,0,None,3,0x80,None)
                                self.assertNotEqual(handle,ctypes.c_void_p(-1).value);held.callback(kernel.CloseHandle,handle)
                            for path,data in (('/api/backups/request?key=request-123',None),('/api/backups/create',{'request_key':'request-123'})):
                                with self.assertRaises(HTTPError) as rejected:request(path,data)
                                self.assertEqual(rejected.exception.code,503 if kind=='locked-status' else 400);rejected.exception.close()
                            self.assertEqual(request('/api/backups')['jobs'],[])
                            self.assertEqual(request('/api/health')['app']['pid'],process.pid)
                            self.assertEqual(outside.read_text(),json.dumps({'state':'completed'}))
                finally:
                    if process.poll() is None:
                        try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=10)
                        except Exception:process.terminate();process.wait(timeout=10)
