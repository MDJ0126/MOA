"""One real inference workflow using the existing saved team; never changes settings."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import hashlib
import argparse
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser();parser.add_argument('--vm',action='store_true');parser.add_argument('--task');options=parser.parse_args()
def call(path,data=None):
    req=urllib.request.Request('http://127.0.0.1:8765'+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
for _ in range(240):
    try:state=call('/api/state');break
    except OSError:time.sleep(.5)
else:raise RuntimeError('App restart did not complete')
if state['paused']:raise RuntimeError('Workspace is paused; verification does not change that setting')
started=time.time()
acceptance="import pathlib,unittest\nclass Acceptance(unittest.TestCase):\n def test_content(self):\n  self.assertEqual(pathlib.Path('ready.md').read_text(encoding='utf-8').strip(),'# Ready\\nThe team is ready.')\n"
tid=options.task or call('/api/tasks',{'title':'[고도화 검증] Create ready.md with EXACTLY two lines, preserving the newline: first line # Ready ; second line The team is ready. Do not join the lines. No Python files are needed.','mode':'llama','required_files':['ready.md'],'acceptance':acceptance,'environment':'vm' if options.vm else 'local'})['id']
print('Actual model smoke task: '+tid,flush=True)
for _ in range(1200):
    state=call('/api/state');task=next(t for t in state['tasks'] if t['id']==tid)
    if task['status'] in ('completed','failed','cancelled','interrupted'):break
    time.sleep(.5)
else:raise RuntimeError('Real inference verification timed out')
result={'task_id':tid,'status':task['status'],'elapsed_seconds':round(time.time()-started,2),'team':json.loads(task['execution_plan']),
        'verification':json.loads(task['verification']),'slack_state':state['slack_status']['state']}
path=ROOT/'data'/'tasks'/tid/'workspace'/'ready.md'
if path.exists():result['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
if options.vm:
    result['vm_evidence']=call('/api/vm/evidence?task='+tid)['files']
    assert len(result['vm_evidence'])>=len(result['team']['pipeline'])*2
(ROOT/'data'/('vm-model-live.json' if options.vm else 'refinement-live.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False),flush=True)
assert task['status']=='completed',task['error']
assert result['verification']['passed']
