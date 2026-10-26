import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from socket import socket
import urllib.request
from tests.test_runtime import node_executable
from soak import freeze

root=Path(__file__).resolve().parent.parent
node=node_executable()
with socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
with tempfile.TemporaryDirectory(dir=root) as data:
    project=Path(data)/'source';freeze(project)
    iso=project/'runtime/vm-downloads/moa-desktop.iso';iso.parent.mkdir(parents=True);iso.write_bytes(b'DOM fixture only; not a bootable ISO')
    process=subprocess.Popen([sys.executable,str(project/'src/app.py'),'--port',str(port),'--data',str(Path(data)/'data')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:urllib.request.urlopen(f'http://127.0.0.1:{port}/api/setup',timeout=2).close();break
            except OSError:
                if process.poll() is not None:raise RuntimeError(process.communicate())
                time.sleep(.1)
        request=urllib.request.Request(f'http://127.0.0.1:{port}/api/onboarding',b'{"action":"skip"}',{'Content-Type':'application/json','X-Local-Agent':'1'})
        urllib.request.urlopen(request,timeout=5).close()
        result=subprocess.run([str(node),'tests/test_ui.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(result.stdout);print(result.stderr)
        if result.returncode:raise SystemExit(result.returncode)
        drafts=subprocess.run([str(node),'tests/test_drafts.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(drafts.stdout);print(drafts.stderr)
        if drafts.returncode:raise SystemExit(drafts.returncode)
        steps=subprocess.run([str(node),'tests/test_vm_steps.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(steps.stdout);print(steps.stderr)
        if steps.returncode:raise SystemExit(steps.returncode)
        profiles=subprocess.run([str(node),'tests/test_execution_profiles.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(profiles.stdout);print(profiles.stderr)
        if profiles.returncode:raise SystemExit(profiles.returncode)
        vbox=subprocess.run([str(node),'tests/test_vbox_setup.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(vbox.stdout);print(vbox.stderr)
        if vbox.returncode:raise SystemExit(vbox.returncode)
        install=subprocess.run([str(node),'tests/test_vbox_install.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(install.stdout);print(install.stderr)
        if install.returncode:raise SystemExit(install.returncode)
        startup=subprocess.run([str(node),'tests/test_startup_drafts.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(startup.stdout);print(startup.stderr)
        if startup.returncode:raise SystemExit(startup.returncode)
        recovery=subprocess.run([str(node),'tests/test_team_recovery.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(recovery.stdout);print(recovery.stderr)
        if recovery.returncode:raise SystemExit(recovery.returncode)
        footer=subprocess.run([str(node),'tests/test_global_work_status.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(footer.stdout);print(footer.stderr)
        if footer.returncode:raise SystemExit(footer.returncode)
        leader=subprocess.run([str(node),'tests/test_leader_inbox.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(leader.stdout);print(leader.stderr)
        if leader.returncode:raise SystemExit(leader.returncode)
        office=subprocess.run([str(node),'tests/test_office.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(office.stdout);print(office.stderr)
        if office.returncode:raise SystemExit(office.returncode)
        idle=subprocess.run([str(node),'tests/test_office_life.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(idle.stdout);print(idle.stderr)
        if idle.returncode:raise SystemExit(idle.returncode)
        resources=subprocess.run([str(node),'tests/test_resources.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(resources.stdout);print(resources.stderr)
        if resources.returncode:raise SystemExit(resources.returncode)
        survey=subprocess.run([str(node),'tests/test_onboarding.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(survey.stdout);print(survey.stderr)
        if survey.returncode:raise SystemExit(survey.returncode)
        organization=subprocess.run([str(node),'tests/test_organization.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(organization.stdout);print(organization.stderr)
        if organization.returncode:raise SystemExit(organization.returncode)
        portraits=subprocess.run([str(node),'tests/test_profile_candidates.cjs'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(portraits.stdout);print(portraits.stderr)
        if portraits.returncode:raise SystemExit(portraits.returncode)
        automatic=subprocess.run([str(node),'tests/test_slack_auto.cjs',f'http://127.0.0.1:{port}'],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        print(automatic.stdout);print(automatic.stderr)
        if automatic.returncode:raise SystemExit(automatic.returncode)
    finally:
        process.terminate();process.wait(timeout=10)
