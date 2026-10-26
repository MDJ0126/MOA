"""Run UI and HTTP verification in an isolated temporary app instance."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
import json
import socket
import subprocess
import tempfile
import time
import urllib.request
from app import Store
from tests.test_runtime import node_executable

with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
with tempfile.TemporaryDirectory(dir=ROOT) as folder:
    Store(folder).control('pause')
    process=subprocess.Popen([sys.executable,str(ROOT/'src/app.py'),'--demo','--port',str(port),'--data',folder],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/state',timeout=1):break
            except OSError:
                if process.poll() is not None:raise RuntimeError(process.communicate())
                time.sleep(.1)
        subprocess.run([node_executable(),'tests/test_prompt_refinement.cjs',f'http://127.0.0.1:{port}'],cwd=ROOT,check=True,timeout=35)
    finally:
        process.terminate();process.wait(timeout=10)
