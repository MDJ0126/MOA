from pathlib import Path
import shutil
import subprocess
import sys
import unittest


class PythonRuntimeTests(unittest.TestCase):
    def run_helper(self,path):
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if not shell:self.skipTest('PowerShell unavailable')
        helper=Path(__file__).parent.parent/'scripts/PythonRuntime.ps1'
        quoted=lambda value:"'"+str(value).replace("'","''")+"'"
        command="$ErrorActionPreference='Stop'; . "+quoted(helper)+"; Resolve-MoaPython -PythonPath "+quoted(path)
        return subprocess.run([shell,'-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)

    def test_explicit_python_is_validated(self):
        result=self.run_helper(sys.executable)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(Path(result.stdout.strip()),Path(sys.executable))

    def test_invalid_override_has_readable_error(self):
        result=self.run_helper(Path(__file__).parent.parent/'missing-python.exe')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('MOA_PYTHON',result.stderr)
