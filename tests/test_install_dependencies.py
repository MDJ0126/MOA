import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from install_dependencies import prune_metadata,install
from supervisor import acquire


class InstallDependencyTests(unittest.TestCase):
    def test_busy_install_never_starts_pip_and_releases_for_next_attempt(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            root=Path(temporary);runtime=root/'runtime';runtime.mkdir()
            with patch('install_dependencies.ROOT',root),patch('install_dependencies.subprocess.run',return_value=subprocess.CompletedProcess([],5)) as pip:
                with acquire(runtime/'dependencies.lock'):
                    with self.assertRaisesRegex(ValueError,'설치 중'):install(['fixture'])
                    pip.assert_not_called()
                self.assertEqual(install(['fixture']),5);pip.assert_called_once()
                with acquire(runtime/'dependencies.lock'):pass
    def metadata(self,target,folder,name,version):
        path=target/folder;path.mkdir(parents=True)
        (path/'METADATA').write_text(f'Name: {name}\nVersion: {version}\n',encoding='utf-8')
        return path

    def test_cleanup_preserves_current_other_packages_and_module_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            target=Path(temporary)/'libs'
            old=self.metadata(target,'fixture-1.dist-info','fixture','1')
            current=self.metadata(target,'fixture-2.dist-info','fixture','2')
            other=self.metadata(target,'other-1.dist-info','other','1')
            module=target/'fixture.py';module.write_bytes(b'current module')
            prune_metadata(target,{'install':[{'metadata':{'name':'fixture','version':'2'}}]})
            self.assertFalse(old.exists());self.assertTrue(current.exists());self.assertTrue(other.exists());self.assertEqual(module.read_bytes(),b'current module')

    def test_linked_old_metadata_is_rejected_without_removing_any_folder(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            root=Path(temporary);target=root/'libs'
            old=self.metadata(target,'fixture-1.dist-info','fixture','1')
            outside=root/'private';outside.write_bytes(b'preserve')
            os.link(outside,old/'RECORD')
            with self.assertRaisesRegex(ValueError,'링크'):prune_metadata(target,{'install':[{'metadata':{'name':'fixture','version':'2'}}]})
            self.assertTrue(old.exists());self.assertEqual(outside.read_bytes(),b'preserve')

    def test_failed_install_never_prunes_existing_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            root=Path(temporary)
            old=self.metadata(root/'runtime/pythonlibs','fixture-1.dist-info','fixture','1')
            with patch('install_dependencies.ROOT',root),patch('install_dependencies.subprocess.run',return_value=subprocess.CompletedProcess([],7)),patch('install_dependencies.prune_metadata') as cleanup:
                self.assertEqual(install(['fixture']),7);cleanup.assert_not_called()
            self.assertTrue(old.exists());self.assertEqual(list((root/'runtime').glob('dependency-*')),[])

    def test_reinstall_updates_existing_project_dependency_without_network(self):
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if not shell:self.skipTest('PowerShell unavailable')
        root=Path(__file__).parent.parent
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            project=Path(temporary)/'프로젝트 with spaces';project.mkdir()
            for name in ('Install.ps1','PythonRuntime.ps1','install_dependencies.py','supervisor.py','app_lifecycle.py'):shutil.copyfile(root/name,project/name)
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
                (project/'requirements.txt').write_text(wheel.as_uri(),encoding='utf-8')
                environment=dict(os.environ,MOA_PYTHON=sys.executable,PIP_NO_INDEX='1',PIP_CONFIG_FILE=os.devnull,PIP_DISABLE_PIP_VERSION_CHECK='1')
                result=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(project/'Install.ps1')],cwd=project,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                installed=project/'runtime/pythonlibs/moa_install_fixture/__init__.py'
                self.assertEqual(installed.read_text(),f'VERSION = "{version}"\n')
                metadata=list((project/'runtime/pythonlibs').glob('moa_install_fixture-*.dist-info'))
                self.assertEqual([path.name for path in metadata],[distribution])
                self.assertFalse((project/'models').exists())

