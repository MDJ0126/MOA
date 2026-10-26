"""Actual local-model + QEMU acceptance run; isolated data, no Slack/Git posting."""
import json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from app import Store,Worker
import app
from guest_agent import GuestAgent
from setup_config import team_settings,write_setting
from vm_runtime import DEFAULT,VM

def main():
 folder=ROOT/'reports/leader-review-qa-20261025';folder.mkdir(exist_ok=True)
 store=Store(folder/'data');config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
 resume='--resume' in sys.argv
 if not resume:
  with store.connect() as c:c.execute("UPDATE tasks SET status='interrupted',error='이전 QA 시도 중단: 최신 검토 정책으로 별도 재검증' WHERE status IN ('running','queued')")
 write_setting(store,'paused',False)
 write_setting(store,'vm',{**DEFAULT,'enabled':True})
 agents=team_settings(store)['agents']
 for agent in agents:
  if agent['id']=='developer':agent['id']='builder'
  elif agent['id']=='reviewer':agent['id']='checker'
 title='Create add.sh with exactly one line: echo $(($1 + $2)). End with newline. Developer: write that line using single quotes, then run sh add.sh 2 3. A different reviewer must independently run test "$(sh add.sh 2 3)" = 5 && test "$(sh add.sh -2 1)" = -1 && test "$(sh add.sh 0 0)" = 0 && echo REVIEW_PASS. No network, no background commands. Leader: review the actual output twice before final approval; preserve correct files and request targeted corrections only for real defects.'
 decision={'action':'delegate','work_kind':'code','summary':'개발 담당자가 셸 파일을 구현하고 다른 검증 담당자가 세 조건을 독립 실행합니다. 리더가 반복 검토 후 승인합니다.','answer':'','agents':agents,'leader_id':'planner','request_id':'quality-qa','assignments':[{'role':'developer','agent_id':'builder','instruction':'add.sh 한 파일만 구현하고 sh add.sh 2 3을 실제 VM에서 실행하세요.'},{'role':'reviewer','agent_id':'checker','instruction':'원문의 세 독립 검증 명령 전체를 실제 VM에서 실행하고 파일은 수정하지 마세요.'}]}
 if resume:
  with store.connect() as c:
   row=c.execute('SELECT id,status FROM tasks ORDER BY created DESC LIMIT 1').fetchone();tid=row['id']
   if row['status']!='queued' or not c.execute('SELECT 1 FROM leader_review_pending WHERE task_id=?',(tid,)).fetchone():raise ValueError('재개할 리더 검토 대기 작업이 없습니다.')
 else:tid=store.add(title,'llama',required_files=['add.sh'],environment='guest',leader_decision=decision,auto_recover=False)
 worker=Worker(store,config['model'],config['model_url']);started=time.time()
 # Reuse the idle installed VM under its existing admission lock. Only the QA
 # task/status connection is isolated; never copy session ownership records.
 class SharedVMStore:
  root=ROOT/'data'
  connect=store.connect
  def __getattr__(self,name):return getattr(store,name)
 worker.vm=VM(SharedVMStore())
 worker.vm.qmp('query-status')
 class QACommandDriver(GuestAgent):
  def run(self,task,role,history,files,log):
   command="echo 'echo $(($1 + $2))' > add.sh && sh add.sh 2 3" if role=='developer' else 'test "$(sh add.sh 2 3)" = 5 && test "$(sh add.sh -2 1)" = -1 && test "$(sh add.sh 0 0)" = 0 && echo REVIEW_PASS'
   log('guest_plan','고정 QA 명령을 실제 VM에서 실행합니다. 리더 검토는 실제 모델을 사용합니다.')
   result=self.vm.command(task['id'],role,command);log('guest_result',json.dumps(result,ensure_ascii=False))
   return {'summary':'실제 VM 명령·종료 코드와 출력 확인','passed':result['exit_code']==0,'guest_results':[result]}
 app.GuestAgent=QACommandDriver
 try:
  tick=0
  while tick<20 and time.time()-started<900:
   progressed=worker.tick();row=next(t for t in store.snapshot()['tasks'] if t['id']==tid)
   if not progressed:time.sleep(1);continue
   print(json.dumps({'tick':tick,'task_id':tid,'status':row['status'],'stage':row['stage'],'revision':row['revision'],'wait_reason':row['wait_reason'],'quality':row['quality_review'],'error':row['error']},ensure_ascii=False),flush=True)
   if row['status'] in ('completed','failed','interrupted','cancelled'):break
   tick+=1
  result={'task_id':tid,'elapsed_seconds':time.time()-started,'executor':'deterministic QA commands in actual QEMU VM','leader':'actual local model decisions','task':row}
  (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
  if row['status']!='completed' or row['quality_review']['review_count']<2:raise SystemExit('Actual QA did not pass')
 finally:
  pass  # The shared installed VM belongs to MOA; leave it running.

if __name__=='__main__':main()
