"""Isolated real-model, public-image and headless VM regression evidence."""
import copy,json,time,sqlite3,os
from pathlib import Path
from app import Store,Worker
from setup_config import save_team,write_setting,team_settings
from leader_inbox import LeaderInbox
from office_chat import OfficeChat
ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'reports/routing-progress-20261025';folder.mkdir(exist_ok=True)
store=Store(folder/'data');production=Store(ROOT/'data');save_team(store,{'agents':team_settings(production)['agents'],'pipeline':['planner','developer','reviewer','reporter']})
with production.connect() as c:vm=json.loads(c.execute("SELECT value FROM settings WHERE key='vm'").fetchone()[0])
write_setting(store,'vm',vm)
write_setting(store,'paused',False)
with store.connect() as c:c.execute("UPDATE tasks SET status='failed',error='QA readiness probe failed before work execution' WHERE status IN ('queued','running')")
config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'));OfficeChat(store,'','',True)
inbox=LeaderInbox(store,config['model'],config['model_url']);agents=team_settings(store)['agents']
started=time.time();title='해바라기 사진 한 장을 찾아서 출처와 함께 전달해 주세요.'
worker=Worker(store,config['model'],config['model_url'])
print('BOOT isolated headless VM before waking model',flush=True)
with worker.vm.admission():worker.vm._start()
print('ROUTING real local model',flush=True)
decision=json.loads((folder/'first-routing.json').read_text(encoding='utf-8')) if os.environ.get('MOA_QA_ROUTE_CACHE') else inbox.decide({'title':title,'agents':agents});(folder/'routing.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(decision,ensure_ascii=False),flush=True)
assert decision['work_kind']=='image',decision
tid=store.add(title,'llama',environment='guest',auto_recover=False,max_revisions=0,leader_decision={**decision,'agents':agents,'leader_id':next(a['id'] for a in agents if not a.get('manager_id')),'request_id':'isolated-real-qa'})
print('TASK '+tid,flush=True)
try:
 worker.tick()
 with store.connect() as c:
  task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone());events=[dict(r) for r in c.execute('SELECT kind,content,created,agent FROM messages WHERE task_id=? ORDER BY seq',(tid,))]
 result={'task_id':tid,'status':task['status'],'error':task['error'],'elapsed_seconds':round(time.time()-started,2),'verification':json.loads(task['verification'] or '{}'),'events':events,'scope':'Isolated actual local model, public sunflower image, actual headless QEMU guest receipt and independent source re-fetch. Production tasks and Slack untouched.'}
 (folder/'live-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print('RESULT '+json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
 assert task['status']=='completed',task['error']
except Exception:
 if worker.vm.session():worker.vm.capture(folder/'qa-failure-vm.png')
 raise
finally:
 if worker.vm.session():worker.vm.stop()
