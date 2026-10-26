"""Same current-policy full workflows, toggling only routing/idle optimization."""
import os,sys,json,time,sqlite3,hashlib,datetime,subprocess,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.environ['MOA_BENCH_OUT']='reports/leader-preparation-20261025'
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tools'))
import benchmark_full_workflow as bench
import audit_full_workflow as audit
from app import Store
from setup_config import write_setting
from supervisor import acquire
OUT=ROOT/os.environ['MOA_BENCH_OUT']
def switches(enabled):
 store=Store(ROOT/'data');write_setting(store,'routing_cache_enabled',enabled);write_setting(store,'assessment_idle_seconds',int(os.environ.get('MOA_ASSESSMENT_IDLE','60')) if enabled else 0)
def restart(phase):
 state=bench.call('/api/state');assert not any(t['status'] in ('queued','running') for t in state['tasks'])
 with bench.db() as c:assert not c.execute("SELECT 1 FROM leader_requests WHERE state IN ('queued','planning')").fetchone()
 bench.call('/api/control',{'action':'pause'})
 started=time.perf_counter()
 subprocess.run([sys.executable,'src/stop_app.py'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
 for i in range(90):
  try:
   with urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=1) as response:response.read()
  except OSError:break
  time.sleep(.5)
 else:raise RuntimeError('App did not stop')
 deadline=time.monotonic()+300
 while True:
  try:
   lease=acquire(ROOT/'data/supervisor.lock');lease.close();break
  except OSError:
   if time.monotonic()>deadline:raise RuntimeError('Supervisor lock did not release')
   time.sleep(.2)
 time.sleep(2)
 log=open(OUT/'runtime.log','ab')
 child=subprocess.Popen([sys.executable,'src/supervisor.py'],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
 for i in range(120):
  if child.poll() is not None:raise RuntimeError('Supervisor exited during startup: '+str(child.returncode))
  try:
   with urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=1) as response:state=json.load(response)
   if state['slack_status']['state']=='connected':break
  except OSError:pass
  time.sleep(.5)
 else:raise RuntimeError('Slack did not reconnect')
 health=bench.call('/api/health');assert health['source_status']['state']=='matching'
 seconds=round(time.perf_counter()-started,3)
 (OUT/(phase+'-restart.json')).write_text(json.dumps({'seconds':seconds,'pid':child.pid,'health':health},ensure_ascii=False,indent=2),encoding='utf-8')
 print(phase+': app restart and Slack readiness '+str(seconds)+' seconds',flush=True)
 return seconds

def prepare(phase):
 state=bench.call('/api/state');assert not any(t['status'] in ('queued','running') for t in state['tasks'])
 with bench.db() as c:assert not c.execute("SELECT 1 FROM leader_requests WHERE state IN ('queued','planning')").fetchone()
 assert state['slack_status']['state']=='connected'
 bench.call('/api/control',{'action':'resume'})
 print(phase+': equal 75-second idle interval before preparation',flush=True)
 time.sleep(75)
 admission_started=time.perf_counter()
 with bench.db() as c:
  pre_pause_jobs={table:[dict(r) for r in c.execute('SELECT task_id,rater,state,updated FROM '+table+" WHERE state='running'")] for table in ['peer_score_jobs','affinity_score_jobs']}
 bench.call('/api/control',{'action':'pause'})
 with bench.db() as c:
  jobs={table:[dict(r) for r in c.execute('SELECT task_id,rater,state,updated FROM '+table+" WHERE state='running'")] for table in ['peer_score_jobs','affinity_score_jobs']}
 health=bench.call('/api/health')
 proof={'phase':phase,'optimization_enabled':phase=='after','followup_interval_seconds':75,'running_assessments_before_priority_transition':pre_pause_jobs,'running_assessments_at_submission':jobs,'health':health,'source':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'src').glob('*.py')}}
 (OUT/(phase+'-environment.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
 return admission_started
def check_policy(phase,restart_seconds=0,whole_seconds=None):
 record=json.loads((OUT/(phase+'.json')).read_text(encoding='utf-8'))
 with bench.db() as c:
  task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(record['task_id'],)).fetchone());reviews=[dict(r) for r in c.execute('SELECT round,decision,checks FROM leader_reviews WHERE task_id=? ORDER BY round',(record['task_id'],))]
 assert len(reviews)>=2 and reviews[-1]['decision']=='approve',reviews
 assert all(x.get('passed') is True for x in json.loads(reviews[-1]['checks']))
 result=audit.audit(phase);assert all(result['checks'].values())
 result['leader_reviews']=reviews;result['current_policy_approved']=True
 result['app_restart_seconds']=restart_seconds
 result['preparation_seconds']=round(restart_seconds+record['vm_preparation_seconds']+record['model_preparation_seconds'],3)
 result['component_sum_seconds']=round(result['elapsed_seconds']+result['preparation_seconds'],3)
 result['including_preparation_seconds']=round(restart_seconds+whole_seconds,3) if whole_seconds is not None else result['component_sum_seconds']
 result['preparation_seconds']=round(result['including_preparation_seconds']-result['elapsed_seconds'],3)
 result['routing_cache']=record['leader_decision'].get('routing_cache')
 (OUT/(phase+'-audit.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print('POLICY '+json.dumps({k:result[k] for k in ['phase','including_preparation_seconds','leader_seconds','routing_cache','quality_passed','current_policy_approved']},ensure_ascii=False),flush=True)
 return result
if __name__=='__main__':
 try:
  for phase,enabled in [('before',False),('after',True)]:
   switches(enabled);restart_seconds=restart(phase);admission_started=prepare(phase);bench.run(phase);whole_seconds=time.perf_counter()-admission_started;check_policy(phase,restart_seconds,whole_seconds)
 finally:switches(True)
