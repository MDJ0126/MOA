import subprocess
import sys
import tempfile
import time
from pathlib import Path
from socket import socket
import urllib.request
from test_runtime import node_executable

root=Path(__file__).resolve().parent
node=node_executable()
with socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
with tempfile.TemporaryDirectory(dir=root) as data:
    process=subprocess.Popen([sys.executable,str(root/'app.py'),'--port',str(port),'--data',data],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:urllib.request.urlopen(f'http://127.0.0.1:{port}/api/setup',timeout=2).close();break
            except OSError:
                if process.poll() is not None:raise RuntimeError(process.communicate())
                time.sleep(.1)
        result=subprocess.run([str(node),'test_ui.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(result.stdout);print(result.stderr)
        if result.returncode:raise SystemExit(result.returncode)
        drafts=subprocess.run([str(node),'test_drafts.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(drafts.stdout);print(drafts.stderr)
        if drafts.returncode:raise SystemExit(drafts.returncode)
    finally:
        process.terminate();process.wait(timeout=10)
