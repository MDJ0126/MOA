"""Crash only our supervised app during an actual task; verify automatic recovery."""
import json
import os
from pathlib import Path
import signal
import time
import urllib.request

ROOT=Path(__file__).resolve().parent

def call(path,data=None):
    req=urllib.request.Request('http://127.0.0.1:8765'+path,None if data is None else json.dumps(data).encode(),
                              {'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)

def main():
    started=time.time()
    call('/api/control',{'action':'resume'})
    tid=call('/api/tasks',{'title':'[실제 복구 검증] Create recovery_note.md containing the line Recovery verified. No Python files are needed. This tests app restart and file reporting.','mode':'llama','auto_recover':True})['id']
    for _ in range(100):
        state=call('/api/state');task=next(t for t in state['tasks'] if t['id']==tid)
        if task['status']=='running':break
        time.sleep(.1)
    else:raise RuntimeError('작업 실행이 시작되지 않았습니다.')
    supervisor=json.loads((ROOT/'data'/'supervisor.json').read_text(encoding='utf-8'))
    assert time.time()-supervisor['updated']<5,'stale supervisor status'
    oldpid=supervisor['children']['app']['pid']
    os.kill(oldpid,signal.SIGTERM)
    print(f'Injected app crash: pid={oldpid}, task={tid}',flush=True)
    for _ in range(1800):
        try:
            state=call('/api/state');task=next(t for t in state['tasks'] if t['id']==tid)
            newpid=state['supervisor']['children']['app']['pid']
            if task['status']=='failed':raise RuntimeError(task)
            if newpid!=oldpid and task['status']=='completed':break
        except (OSError,KeyError,TypeError):pass
        time.sleep(.1)
    else:raise RuntimeError('자동 복구가 시간 내 완료되지 않았습니다.')
    p=ROOT/'data'/'tasks'/tid/'workspace'/'recovery_note.md'
    assert 'Recovery verified' in p.read_text(encoding='utf-8')
    result={'task_id':tid,'old_app_pid':oldpid,'new_app_pid':newpid,
            'app_restarts':state['supervisor']['restarts']['app'],'status':task['status'],
            'recovery_count':task['failures'],'elapsed_seconds':round(time.time()-started,2)}
    assert task['failures']>=1
    (ROOT/'data'/'recovery-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
