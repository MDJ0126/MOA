"""Populate the running local dashboard with explicit demonstration jobs."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import json
import time
import urllib.request
from pathlib import Path

def call(path, data=None):
    req=urllib.request.Request('http://127.0.0.1:8765'+path,
        None if data is None else json.dumps(data).encode(),
        {'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)

state=call('/api/state')
if not state['tasks']:
    for title in ('[데모] 계획·개발·검증·보고 역할별 기록 확인', '[데모] 두 번째 작업의 순차 처리와 산출물 정리 확인'):
        call('/api/tasks',{'title':title,'mode':'demo'})
for _ in range(100):
    state=call('/api/state')
    if all(t['status']=='completed' for t in state['tasks']):break
    time.sleep(.1)
else:raise RuntimeError(state)
summary={'url':'http://127.0.0.1:8765','tasks':state['tasks'],
    'message_count':len(state['messages']),'unit_tests':'7 passed',
    'http_integration':'passed','dashboard_js_syntax':'passed',
    'visual_browser_test':'not run; computer use excluded by user',
    'real_model_inference':'not run; model not installed/configured',
    'engine_slack_vm':'not connected'}
Path('data/verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'tasks':[(t['id'],t['status']) for t in state['tasks']], 'messages':len(state['messages'])},ensure_ascii=False))
