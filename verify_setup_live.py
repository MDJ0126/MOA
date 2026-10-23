"""Apply the hardware-recommended team and run one real configured workflow."""
import hashlib
import json
from pathlib import Path
import time
import urllib.request

root=Path(__file__).resolve().parent
def call(path,data=None):
    request=urllib.request.Request('http://127.0.0.1:8765'+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)

setup=call('/api/setup')
recommended=next(p for p in setup['presets'] if p['id']==setup['hardware']['recommended_preset'])
call('/api/setup/team',recommended)
call('/api/control',{'action':'resume'})
acceptance="import pathlib,unittest\nclass DocumentAcceptance(unittest.TestCase):\n def test_exact_content(self):\n  self.assertEqual(pathlib.Path('welcome.md').read_text(encoding='utf-8').strip(),'# Team ready\\nThe local agent team is ready.')\n"
tid=call('/api/tasks',{'title':'[설정 검증] Write welcome.md with EXACTLY these two lines, preserving the newline:\n# Team ready\nThe local agent team is ready.\nDo not join the two lines. No Python files are needed.','required_files':['welcome.md'],'acceptance':acceptance,'mode':'llama'})['id']
print('Real configured task: '+tid,flush=True)
for _ in range(1800):
    state=call('/api/state');task=next(t for t in state['tasks'] if t['id']==tid)
    if task['status'] in ('completed','failed'):break
    time.sleep(.1)
else:raise RuntimeError('Configured task timed out')
assert task['status']=='completed',task
plan=json.loads(task['execution_plan']);assert len(plan['agents'])==recommended['count']
starts=[m for m in state['messages'] if m['task_id']==tid and m['kind']=='start']
assert len(starts)==len(recommended['pipeline'])
assert all(m['agent'] in {a['id'] for a in plan['agents']} for m in starts)
content=(root/'data'/'tasks'/tid/'workspace'/'welcome.md').read_text(encoding='utf-8')
assert content.splitlines()[0]=='# Team ready',content
result={'task_id':tid,'preset':recommended['id'],'agent_count':recommended['count'],'pipeline':plan['pipeline'],
        'actual_agent_ids':[m['agent'] for m in starts],'status':task['status'],'file_sha256':hashlib.sha256((root/'data'/'tasks'/tid/'workspace'/'welcome.md').read_bytes()).hexdigest(),
        'verification':json.loads(task['verification']),'slack_status':state['slack_status'],
        'scope':'real configured local model run; actual Slack account credentials not supplied'}
(root/'data'/'setup-live-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False),flush=True)
