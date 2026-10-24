"""Generate guide screenshots from a frozen local source with sample data."""
import os
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from app import Store,Worker
from setup_config import save_team,PRESETS
from soak import freeze
from tests.test_runtime import node_executable

ROOT=Path(__file__).resolve().parent

def main():
    output=ROOT/'docs'/'screenshots';output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        project=Path(temporary)/'source';freeze(project)
        data=Path(temporary)/'data';store=Store(data);save_team(store,PRESETS[1])
        store.add('샘플 작업 · 체력 제한 흐름 확인','demo');Worker(store).tick()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with (Path(temporary)/'server.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,str(project/'app.py'),'--data',str(data),'--port',str(port),'--model','','--model-url','http://127.0.0.1:0'],stdout=log,stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1):break
                    except OSError:
                        if process.poll() is not None:raise RuntimeError('Screenshot sample app exited')
                        time.sleep(.1)
                else:raise RuntimeError('Sample app readiness timeout')
                environment=dict(os.environ,MOA_CAPTURE_SERVE_DIR=str(project/'docs'/'screenshots'))
                subprocess.run([str(node_executable()),str(ROOT/'capture_guides.cjs'),f'http://127.0.0.1:{port}',str(output)],env=environment,check=True,timeout=100)
                manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
                manifest['captured_at']=datetime.now(timezone.utc).isoformat()
                manifest['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('dashboard.html','ui.js','ui.css')}
                manifest['image_sha256']={name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in manifest['files']}
                (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=10)

if __name__=='__main__':main()
