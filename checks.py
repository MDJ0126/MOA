"""Execute Python verification in a child process, with a bounded duration.
This is process separation, NOT an OS security sandbox.
"""
import json
from pathlib import Path
import subprocess
import sys

RUNNER = r'''
import ast,contextlib,io,json,pathlib,sys,unittest
root=pathlib.Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
errors=[]
for p in root.rglob('*.py'):
    try:ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
    except Exception as e:errors.append(str(e))
stream=io.StringIO()
count=0;success=not errors
try:
    with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
        suite=unittest.defaultTestLoader.discover(str(root),pattern='test_*.py')
        if sys.argv[2]:
            spec=pathlib.Path(sys.argv[2]);namespace={'__name__':'acceptance','__file__':str(spec)}
            exec(compile(spec.read_text(encoding='utf-8'),str(spec),'exec'),namespace)
            suite.addTests(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(v) for v in namespace.values() if isinstance(v,type) and issubclass(v,unittest.TestCase) and v is not unittest.TestCase))
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        count=result.testsRun;success=success and result.wasSuccessful() and count>0
except BaseException as e:success=False;errors.append(repr(e))
print(json.dumps({'passed':success,'tests_run':count,'errors':errors,'output':stream.getvalue()[-20000:]}))
'''

def run_checks(folder, acceptance='', timeout=30):
    folder=Path(folder).resolve()
    if not any(folder.rglob('*.py')) and not acceptance:
        return {'passed':True,'tests_run':0,'scope':'file review only; no executable tests','output':''}
    acceptance_file=''
    if acceptance:
        spec=folder.parent/'acceptance.py'
        spec.write_text(acceptance,encoding='utf-8');acceptance_file=str(spec)
    try:
        run=subprocess.run([sys.executable,'-I','-c',RUNNER,str(folder),acceptance_file],cwd=folder,
                           capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        if run.returncode!=0:return {'passed':False,'tests_run':0,'output':run.stderr[-10000:],'returncode':run.returncode}
        result=json.loads(run.stdout.strip().splitlines()[-1]);result['scope']='Python syntax + '+('fixed acceptance tests' if acceptance else 'workspace unittest tests')
        return result
    except (subprocess.TimeoutExpired,ValueError,IndexError) as e:
        return {'passed':False,'tests_run':0,'output':str(e),'scope':'verification process failed or timed out'}
