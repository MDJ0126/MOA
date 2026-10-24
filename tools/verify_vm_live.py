"""Real shared desktop verification through the app API; never touches host input."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import hashlib
import json
import struct
import sys
from pathlib import Path
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parent.parent
def call(path,data=None):
    req=urllib.request.Request('http://127.0.0.1:8765'+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
for _ in range(240):
    try:before=call('/api/vm');break
    except OSError:time.sleep(.5)
else:raise RuntimeError('Updated API did not start')
assert before['state']=='running',before
session=json.loads((ROOT/'data/vm/session.json').read_text())
existing=sys.argv[1] if len(sys.argv)>1 else None
if not existing:
    with urllib.request.urlopen('http://127.0.0.1:8765/api/vm/screen') as response:screen=response.read()
    width,height=struct.unpack('>II',screen[16:24])
else:width,height=1280,800
actions=[{'role':'developer','action':'click','x':round((width/2+98)/width*32767),'y':round((height-24)/height*32767)},{'role':'developer','action':'wait','seconds':2},
         {'role':'developer','action':'text','text':'echo VM_AGENT_BUILDER\n'},
         {'role':'reviewer','action':'text','text':'echo VM_AGENT_CHECKER\n'},
         {'role':'reviewer','action':'wait','seconds':1},
         {'role':'reviewer','action':'capture'}]
tid=existing or call('/api/tasks',{'title':'[공유 VM 검증] 두 에이전트가 한 데스크톱의 입력과 캡처를 차례로 사용합니다.','mode':'demo','environment':'vm','vm_actions':actions})['id']
print('Actual VM task '+tid,flush=True)
for _ in range(1800):
    state=call('/api/state');task=next(t for t in state['tasks'] if t['id']==tid)
    if task['status'] in ('completed','failed','cancelled'):break
    time.sleep(.2)
assert task['status']=='completed',task
with urllib.request.urlopen('http://127.0.0.1:8765/api/logs?task='+tid) as response:logs=json.load(response)['messages']
leases=sorted([m for m in logs if m['kind'] in ('vm_acquire','vm_release')],key=lambda m:m['seq'])
assert [m['kind'] for m in leases]==['vm_acquire','vm_release']*3
assert [m['agent'] for m in leases[::2]]==['builder','builder','checker']
evidence=call('/api/vm/evidence?task='+tid)['files'];assert len(evidence)==6,evidence
for file in evidence:
    path=ROOT/'data/tasks'/tid/file;assert path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
for role in ('developer','reviewer'):
    prefix=ROOT/'data/tasks'/tid/'vm'
    assert (prefix/f'r0-{role}-before.png').read_bytes()!=(prefix/f'r0-{role}-after.png').read_bytes(),role+' produced no observable screen change'
assert session['pid']==json.loads((ROOT/'data/vm/session.json').read_text())['pid']
report=json.loads((ROOT/'data/tasks'/tid/'report.json').read_text(encoding='utf-8'))
assert len(report['vm_evidence'])==6
with zipfile.ZipFile(ROOT/'data/tasks'/tid/'artifacts.zip') as bundle:assert all(file in bundle.namelist() for file in evidence)
result={'task_id':tid,'status':task['status'],'vm_pid':session['pid'],'agents':[m['agent'] for m in leases[::2]],'evidence':evidence,
        'sha256':{file:hashlib.sha256((ROOT/'data/tasks'/tid/file).read_bytes()).hexdigest() for file in evidence},
        'scope':'Actual QEMU guest keyboard, mouse, screenshots, per-agent exclusive handoff. Demo file backend; not model visual reasoning.'}
(ROOT/'data/vm-live-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False),flush=True)
