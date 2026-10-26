"""Real model + actual guest + exported artifact, without pretending Slack is connected."""
import io
import json
from pathlib import Path
import tarfile
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parent.parent

def call(path,data=None):
    request=urllib.request.Request('http://127.0.0.1:8765'+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)

state=call('/api/state')
if state['paused']:raise RuntimeError('먼저 작업 관리자를 재개하세요.')
title='[실제 VM AI 검증] Create ready.txt containing exactly VM_TEAM_READY followed by newline. Developer: run echo VM_TEAM_READY > ready.txt and cat ready.txt. Reviewer: independently run test "$(cat ready.txt)" = VM_TEAM_READY && echo REVIEW_PASS. Keep commands short. No Python, no network, no background jobs.'
started=time.time()
tid=call('/api/tasks',{'title':title,'mode':'llama','environment':'guest','required_files':['ready.txt'],'max_revisions':1,'auto_recover':False})['id']
print('Actual model + guest task: '+tid,flush=True)
for _ in range(1500):
    task=next(t for t in call('/api/state')['tasks'] if t['id']==tid)
    if task['status'] in ('completed','failed','cancelled','interrupted'):break
    time.sleep(.5)
else:raise RuntimeError('모델·게스트 검증 시간 초과: '+tid)
assert task['status']=='completed',task.get('error')
verification=json.loads(task['verification']);assert verification['passed'] and verification['vm']['file_checks_location']=='guest'
with urllib.request.urlopen('http://127.0.0.1:8765/api/artifact?task='+tid+'&file=artifacts.zip',timeout=10) as response:data=response.read()
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    name=next(name for name in archive.namelist() if name.endswith('guest-workspace.tar.gz'))
    with tarfile.open(fileobj=io.BytesIO(archive.read(name)),mode='r:gz') as guest:
        ready=next(member for member in guest if member.name.lstrip('./')=='ready.txt')
        content=guest.extractfile(ready).read().decode('utf-8')
assert content=='VM_TEAM_READY\n',repr(content)
record={'task_id':tid,'status':task['status'],'elapsed_seconds':round(time.time()-started,2),'verification':verification,
        'exported_ready_content':content,'scope':'Actual local model, actual QEMU guest work, independent reviewer, HTTP ZIP and nested guest archive readback. Slack account is not connected.'}
(ROOT/'data/guest-model-live-verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(record,ensure_ascii=False),flush=True)
