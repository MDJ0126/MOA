"""Actual local API -> leader -> model -> VM -> Slack -> exported file comparison."""
import argparse,datetime,hashlib,io,json,sqlite3,sys,tarfile,time,urllib.request,uuid,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/__import__('os').environ.get('MOA_BENCH_OUT','reports/full-performance-20261025')
sys.path.insert(0,str(ROOT/'src'))
from local_model import headers
TITLE='[전체 성능 비교 · 셸 코드 개발] Implement add.sh shell code with exactly one line: echo $(($1 + $2)). End with newline. Developer: write that line using single quotes, then run sh add.sh 2 3. A different reviewer must independently run test "$(sh add.sh 2 3)" = 5 && test "$(sh add.sh -2 1)" = -1 && test "$(sh add.sh 0 0)" = 0 && echo REVIEW_PASS. Share actual VM screenshots and command evidence with the next teammate through Slack. Do not claim success before commands run. No network, no background commands.'
def call(path,data=None):
    request=urllib.request.Request('http://127.0.0.1:8765'+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(request,timeout=20) as r:return json.load(r)
def db():
    c=sqlite3.connect('file:'+str(ROOT/'data/state.sqlite3').replace('\\','/')+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c
def run(phase,wait_for_idle=False):
    OUT.mkdir(exist_ok=True);state=call('/api/state');setup=call('/api/setup');s=setup['slack']
    assert s['enabled'] and s['collaboration'] and state['slack_status']['state']=='connected','Actual Slack is not connected'
    assert not any(t['status'] in ('queued','running') for t in state['tasks']),'Other work active'
    with db() as c:
        assert not c.execute("SELECT 1 FROM leader_requests WHERE state IN ('queued','planning')").fetchone(),'Other leader work pending; do not contaminate full-workflow comparison'
    paused=state['paused'];tid=None;request_id=None;started=time.perf_counter()
    record={'phase':phase,'title':TITLE,'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'initial_paused':paused,'scope':'Actual production API leader routing, model, real VM commands, Slack handoffs and images, independent reviewer, final exported ZIP readback. Both phases prepare the VM before task submission; preparation time is separate. Baseline already includes previous leader/cache tuning.'}
    def save():
        record['elapsed_seconds']=round(time.perf_counter()-started,3)
        (OUT/(phase+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        if wait_for_idle:
            deadline=time.monotonic()+330
            while time.monotonic()<deadline:
                with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8766/props',headers=headers('http://127.0.0.1:8766')),timeout=5) as r:props=json.load(r)
                if props.get('is_sleeping'):break
                time.sleep(5)
            else:raise TimeoutError('Model did not release memory for VM preparation')
        # Start the actual guest before waking the CPU model. Its memory safety
        # check remains intact; preparations are recorded separately from task latency.
        preparation=time.perf_counter()
        call('/api/vm/control',{'action':'start'})
        record['vm_preparation_seconds']=round(time.perf_counter()-preparation,3)
        preparation=time.perf_counter()
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8766/health',timeout=3) as response:json.load(response)
                break
            except OSError:time.sleep(.5)
        else:raise TimeoutError('Model readiness timed out before measurement')
        warm={'model':'qwen-local','messages':[{'role':'user','content':'Reply only READY.'}],'temperature':0,'seed':42,'max_tokens':8}
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8766/v1/chat/completions',json.dumps(warm).encode(),headers('http://127.0.0.1:8766')),timeout=300) as r:json.load(r)
        record['model_preparation_seconds']=round(time.perf_counter()-preparation,3)
        started=time.perf_counter()
        receipt=call('/api/tasks',{'title':TITLE,'mode':'llama','environment':'guest','required_files':['add.sh'],'max_revisions':0,'auto_recover':False,'leader_first':True,'request_key':'full_bench_'+uuid.uuid4().hex})
        if paused:call('/api/control',{'action':'resume'})
        request_id=receipt['id'];record['request_id']=request_id;save();previous=None
        deadline=time.monotonic()+1200
        while time.monotonic()<deadline:
            if not tid:
                request=call('/api/leader/request?id='+request_id)
                if request.get('task_id'):
                    tid=request['task_id'];record['task_id']=tid;record['leader_seconds']=round(time.perf_counter()-started,3);record['leader_decision']=request.get('decision');save()
                elif request.get('state') in ('failed','replied'):raise RuntimeError('Leader did not publish work: '+str(request))
            if tid:
                with db() as c:
                    task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
                    events=[dict(r) for r in c.execute('SELECT seq,agent,kind,content,created,capability FROM messages WHERE task_id=? ORDER BY seq',(tid,))]
                    deliveries=[dict(r) for r in c.execute('SELECT o.id,o.state,o.attempts,o.error,o.receipt,d.sender,d.recipient,d.created FROM outbox o JOIN deliveries d ON d.id=o.delivery_id WHERE d.task_id=? ORDER BY o.id',(tid,))]
                    receipts=[dict(r) for r in c.execute('SELECT * FROM slack_direct WHERE task_id=?',(tid,))]
                marker=(task['status'],task['stage'],events[-1]['seq'] if events else 0,sum(d['state']=='sent' for d in deliveries))
                if marker!=previous:
                    print(json.dumps({'phase':phase,'elapsed':round(time.perf_counter()-started,1),'task_id':tid,'status':task['status'],'stage':task['stage'],'last_event':events[-1]['kind'] if events else None,'slack_sent':marker[-1],'slack_total':len(deliveries)},ensure_ascii=False),flush=True);previous=marker
                    record.update(status=task['status'],error=task.get('error'),events=events,deliveries=deliveries,receipts=receipts);save()
                if task['status'] in ('failed','cancelled','interrupted'):raise RuntimeError('Actual work failed: '+task['status']+' '+task.get('error',''))
                if task['status']=='completed' and deliveries and all(d['state']=='sent' for d in deliveries):break
            time.sleep(1)
        else:raise TimeoutError('Actual full workflow exceeded 1200 seconds')
        with urllib.request.urlopen('http://127.0.0.1:8765/api/artifact?task='+tid+'&file=artifacts.zip',timeout=30) as r:bundle=r.read()
        (OUT/(phase+'-artifacts.zip')).write_bytes(bundle)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            names=archive.namelist();nested=next(n for n in names if n.endswith('guest-workspace.tar.gz'))
            with tarfile.open(fileobj=io.BytesIO(archive.read(nested)),mode='r:gz') as guest:
                member=next(m for m in guest if m.name.lstrip('./')=='add.sh');content=guest.extractfile(member).read()
        plan=json.loads(task['execution_plan']);verification=json.loads(task['verification']);actors=plan['assignment']
        checks={'exact_exported_bytes':content==b'echo $(($1 + $2))\n','guest_verification_passed':verification.get('passed') is True,
                'independent_actors':actors.get('developer')!=actors.get('reviewer'),
                'review_command_observed':any(e['kind']=='guest_result' and 'REVIEW_PASS' in e['content'] and e['agent']==actors.get('reviewer') for e in events),
                'all_slack_deliveries_sent':all(d['state']=='sent' and d['receipt'] for d in deliveries),
                'recipient_events_observed':bool(receipts) and all(r['received_event'] and r['images_ready'] for r in receipts),
                'received_images_observed':any(json.loads(r['received_images']) for r in receipts)}
        record.update(checks=checks,verification=verification,artifact_sha256=hashlib.sha256(bundle).hexdigest(),exported_file_sha256=hashlib.sha256(content).hexdigest(),exported_file_content=content.decode(),zip_entries=names,status='passed' if all(checks.values()) else 'quality_failed')
        save();assert all(checks.values()),checks
        print('RESULT '+json.dumps({k:v for k,v in record.items() if k not in ('events','deliveries','receipts','verification','zip_entries','leader_decision')},ensure_ascii=False),flush=True)
    except Exception as e:
        record['failure']=str(e);save();raise
    finally:
        if paused:call('/api/control',{'action':'pause'})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['before','after'],required=True);parser.add_argument('--wait-for-idle',action='store_true');args=parser.parse_args();run(args.phase,args.wait_for_idle)
