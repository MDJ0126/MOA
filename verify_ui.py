import subprocess
import sys
import tempfile
import time
from pathlib import Path
from socket import socket
import urllib.request
from test_runtime import node_executable
from soak import freeze

root=Path(__file__).resolve().parent
node=node_executable()
with socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
with tempfile.TemporaryDirectory(dir=root) as data:
    project=Path(data)/'source';freeze(project)
    iso=project/'runtime/vm-downloads/moa-desktop.iso';iso.parent.mkdir(parents=True);iso.write_bytes(b'DOM fixture only; not a bootable ISO')
    process=subprocess.Popen([sys.executable,str(project/'app.py'),'--port',str(port),'--data',str(Path(data)/'data')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
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
        steps=subprocess.run([str(node),'test_vm_steps.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(steps.stdout);print(steps.stderr)
        if steps.returncode:raise SystemExit(steps.returncode)
        profiles=subprocess.run([str(node),'test_execution_profiles.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(profiles.stdout);print(profiles.stderr)
        if profiles.returncode:raise SystemExit(profiles.returncode)
        vbox=subprocess.run([str(node),'test_vbox_setup.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(vbox.stdout);print(vbox.stderr)
        if vbox.returncode:raise SystemExit(vbox.returncode)
        install=subprocess.run([str(node),'test_vbox_install.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(install.stdout);print(install.stderr)
        if install.returncode:raise SystemExit(install.returncode)
    finally:
        process.terminate();process.wait(timeout=10)
