import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class StartScriptExitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.root=Path(self.temp.name)/'project with spaces';self.root.mkdir()
        (self.root/'scripts').mkdir();(self.root/'src').mkdir()
        for name in ('Start.ps1','scripts/PythonRuntime.ps1'):shutil.copyfile(Path(__file__).parent.parent/name,self.root/name)
        stub="import json,os,sys\nprint(json.dumps({'entry':os.path.basename(__file__),'args':sys.argv[1:]}))\nsys.exit(int(os.environ['MOA_TEST_EXIT']))\n"
        for name in ('app.py','supervisor.py'):(self.root/'src'/name).write_text(stub,encoding='utf-8')
        self.shells=list(dict.fromkeys(path for name in ('pwsh','powershell') if (path:=shutil.which(name))))
        if not self.shells:self.skipTest('PowerShell unavailable')
    def tearDown(self):self.temp.cleanup()

    def launch(self,shell,code,args):
        environment=dict(os.environ,MOA_PYTHON=sys.executable,AGENT_MODEL='',MOA_TEST_EXIT=str(code))
        # Only this child uses Bypass; do not change the user's execution policy.
        result=subprocess.run([shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(self.root/'Start.ps1'),*args],env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        records=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertEqual(len(records),1,result.stderr+'\n'+result.stdout)
        return result,records[0]

    def test_failed_direct_demo_custom_port_and_model_preserve_exit_code(self):
        for shell in self.shells:
            for args in (['-Direct'],['-Demo'],['-Port','12345'],['-Model','custom-model']):
                with self.subTest(shell=shell,args=args):
                    result,record=self.launch(shell,7,args)
                    self.assertEqual(record['entry'],'app.py');self.assertEqual(result.returncode,7,result.stderr)

    def test_normal_direct_exit_stays_success_and_demo_uses_separate_port(self):
        for shell in self.shells:
            with self.subTest(shell=shell):
                result,record=self.launch(shell,0,['-Demo'])
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(record['args'],['--port','8767','--demo'])

    def test_supervisor_branch_preserves_failure(self):
        for shell in self.shells:
            with self.subTest(shell=shell):
                result,record=self.launch(shell,9,[])
                self.assertEqual(record['entry'],'supervisor.py');self.assertEqual(result.returncode,9,result.stderr)
