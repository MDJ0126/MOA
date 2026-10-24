"""Revalidate earlier real runs against explicit required artifact checks."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import json
from pathlib import Path
import time
import urllib.request
from app import ROOT, Store, Worker, CONFIG

def call(action):
    req=urllib.request.Request('http://127.0.0.1:8765/api/control',json.dumps({'action':action}).encode(),
                              {'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=3) as r:return json.load(r)

def main():
    store=Store(ROOT/'data')
    previous=json.loads((ROOT/'data'/'real-verification.json').read_text(encoding='utf-8'))
    ids=previous['tasks']
    call('pause')
    with store.connect() as c:
        for tid in ids:
            c.execute("UPDATE tasks SET status='queued',stage=2,max_revisions=4,required_files=?,error='필수 파일을 포함한 전체 재검증' WHERE id=?",
                      (json.dumps(['health.py','test_health.py']),tid))
    # The server must already be running current sources before this script.
    call('resume')
    started=time.time()
    for _ in range(2400):
        tasks={t['id']:t for t in store.snapshot()['tasks'] if t['id'] in ids}
        if any(t['status']=='failed' for t in tasks.values()):raise RuntimeError(tasks)
        if len(tasks)==len(ids) and all(t['status']=='completed' for t in tasks.values()):break
        time.sleep(.1)
    else:raise RuntimeError('Revalidation timed out')
    result=[]
    for tid,t in tasks.items():
        workspace=ROOT/'data'/'tasks'/tid/'workspace'
        assert (workspace/'test_health.py').is_file()
        verification=json.loads(t['verification'])
        assert verification['passed'] and verification['tests_run']>=4
        result.append({'task_id':tid,'revision':t['revision'],'verification':verification})
    previous['artifact_revalidation']=result
    previous['revalidation_elapsed_seconds']=round(time.time()-started,2)
    (ROOT/'data'/'real-verification.json').write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
