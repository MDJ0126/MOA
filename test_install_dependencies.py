import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


class InstallDependencyTests(unittest.TestCase):
    def test_reinstall_updates_existing_project_dependency_without_network(self):
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if not shell:self.skipTest('PowerShell unavailable')
        root=Path(__file__).parent
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            project=Path(temporary)
            for name in ('Install.ps1','PythonRuntime.ps1'):shutil.copyfile(root/name,project/name)
            for version in ('1.0','2.0'):
                distribution=f'moa_install_fixture-{version}.dist-info'
                files={'moa_install_fixture/__init__.py':f'VERSION = "{version}"\n'.encode(),
                       distribution+'/METADATA':f'Metadata-Version: 2.1\nName: moa-install-fixture\nVersion: {version}\n'.encode(),
                       distribution+'/WHEEL':b'Wheel-Version: 1.0\nGenerator: local-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
                record=''.join(f'{name},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")},{len(data)}\n' for name,data in files.items())
                record_name=distribution+'/RECORD';files[record_name]=(record+record_name+',,\n').encode()
                wheel=project/f'moa_install_fixture-{version}-py3-none-any.whl'
                with zipfile.ZipFile(wheel,'w') as archive:
                    for name,data in files.items():archive.writestr(name,data)
                (project/'requirements.txt').write_text(str(wheel),encoding='utf-8')
                environment=dict(os.environ,MOA_PYTHON=sys.executable,PIP_NO_INDEX='1',PIP_CONFIG_FILE=os.devnull,PIP_DISABLE_PIP_VERSION_CHECK='1')
                result=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(project/'Install.ps1')],cwd=project,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                installed=project/'runtime/pythonlibs/moa_install_fixture/__init__.py'
                self.assertEqual(installed.read_text(),f'VERSION = "{version}"\n')
                self.assertFalse((project/'models').exists())

