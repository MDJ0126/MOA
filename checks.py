"""Bounded Python verification. Process lifetime control is not an OS sandbox."""
import json
import math
from pathlib import Path
import sys
import tempfile
from check_process import execute

RUNNER = r'''
import sys
if sys.stdin.buffer.read(1)!=b'1':raise SystemExit(75)
import ast,contextlib,json,pathlib,unittest
root=pathlib.Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
errors=[];errors_truncated=False
for p in root.rglob('*.py'):
    try:ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
    except Exception as e:
        if len(errors)<20:errors.append(str(e)[:500])
        else:errors_truncated=True
class Tail:
    def __init__(self):self.value='';self.truncated=False
    def write(self,value):
        self.truncated=self.truncated or len(self.value)+len(value)>20000
        self.value=(self.value+value[-20000:])[-20000:];return len(value)
    def flush(self):pass
stream=Tail();count=0;success=not errors
try:
    with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
        suite=unittest.defaultTestLoader.discover(str(root),pattern='test_*.py')
        if sys.argv[2]:
            spec=pathlib.Path(sys.argv[2]);namespace={'__name__':'acceptance','__file__':str(spec)}
            exec(compile(spec.read_text(encoding='utf-8'),str(spec),'exec'),namespace)
            suite.addTests(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(v) for v in namespace.values() if isinstance(v,type) and issubclass(v,unittest.TestCase) and v is not unittest.TestCase))
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        count=result.testsRun;success=success and result.wasSuccessful() and count>0
except BaseException as e:success=False;errors.append(repr(e)[:500])
pathlib.Path(sys.argv[3]).write_text(json.dumps({'passed':success,'tests_run':count,'errors':errors,'errors_truncated':errors_truncated,'output':stream.value,'output_truncated':stream.truncated},ensure_ascii=False),encoding='utf-8')
'''


def run_checks(folder, acceptance='', timeout=30):
    folder=Path(folder).resolve()
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise ValueError('검증 제한 시간은 유한한 양수여야 합니다.')
    if not any(folder.rglob('*.py')) and not acceptance:
        return {'passed':True,'tests_run':0,'scope':'file review only; no executable tests','output':''}
    try:
        with tempfile.TemporaryDirectory(dir=folder.parent) as temporary:
            result_file=Path(temporary)/'result.json';acceptance_file=''
            if acceptance:
                spec=Path(temporary)/'acceptance.py';spec.write_text(acceptance,encoding='utf-8');acceptance_file=str(spec)
            run=execute([sys.executable,'-I','-c',RUNNER,str(folder),acceptance_file,str(result_file)],folder,timeout)
            if run['timed_out'] or run['returncode']!=0:
                return {'passed':False,'tests_run':0,'output':(run['stderr'] or run['stdout'])[-20000:],
                        'errors':[f'Python 검증 제한 시간 초과 ({timeout}초). 검증 프로세스를 정리했습니다.' if run['timed_out'] else 'Python 검증 프로세스가 결과를 저장하기 전에 종료되었습니다.'],
                        'returncode':run['returncode'],'timed_out':run['timed_out'],'native_output_truncated':run['native_output_truncated'],
                        'scope':'verification process timed out' if run['timed_out'] else 'verification process exited before results'}
            with result_file.open('rb') as stream:raw=stream.read(512*1024+1)
            if len(raw)>512*1024:raise ValueError('검증 결과 크기 제한 초과')
            result=json.loads(raw)
            if not isinstance(result,dict) or type(result.get('passed')) is not bool or type(result.get('tests_run')) is not int or result['tests_run']<0 or result['passed'] and result['tests_run']==0:raise ValueError('잘못된 검증 결과')
            result.update(scope='Python syntax + '+('fixed acceptance tests' if acceptance else 'workspace unittest tests'),
                          native_output=(run['stdout']+run['stderr'])[-20000:],native_output_truncated=run['native_output_truncated'])
            return result
    except (OSError,ValueError,TypeError) as error:
        return {'passed':False,'tests_run':0,'output':str(error),'errors':['Python 검증 프로세스를 준비하거나 결과를 확인하지 못했습니다.'],'scope':'verification process setup or result failed'}
