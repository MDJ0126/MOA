import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store,Worker
import leader_review as q

def response(decision='approve',reason='요청과 실제 파일·검증 결과를 대조했습니다.',instructions=''):
 return {'choices':[{'message':{'content':json.dumps({'decision':decision,'reason':reason,'instructions':instructions,'checks':[{'criterion':'원문 결과물','evidence':'answer.txt 본문 확인','passed':decision=='approve'},{'criterion':'검증 결과','evidence':'파일 검사 결과 확인','passed':True}]})},'finish_reason':'stop'}]}

class Backend:
 def __init__(self):self.calls=[];self.instructions=[]
 def run(self,task,role,history,files,log):
  self.calls.append(role);self.instructions.append((role,task.get('leader_instruction','')))
  if role=='planner':return {'summary':'담당자가 파일을 작성하고 검증합니다.','required_files':['answer.txt']}
  if role=='developer':
   files.path('answer.txt').write_text('corrected' if '[리더 재지시' in task.get('leader_instruction','') else 'initial',encoding='utf-8')
   files.path('keep.txt').write_text('preserve',encoding='utf-8')
  return {'summary':'파일을 직접 확인했습니다.','passed':True}

class LeaderQualityTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.store=Store(self.temp.name);self.worker=Worker(self.store,model='qa',url='http://127.0.0.1:8766');self.backend=Backend()
 def tearDown(self):self.temp.cleanup()
 def row(self):return self.store.snapshot()['tasks'][0]
 def run_until_terminal(self,limit=12):
  for _ in range(limit):
   self.worker.tick()
   if self.row()['status'] in ('completed','failed','interrupted'):return self.row()
  self.fail('review loop did not terminate')
 def test_first_approval_forces_real_second_check_and_two_leader_reviews(self):
  tid=self.store.add('answer.txt를 작성해 주세요','llama')
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',return_value=response()) as model:
   self.worker.tick();self.assertEqual(self.row()['status'],'queued');self.assertEqual(self.row()['wait_reason'],'leader_review')
   self.assertFalse(any(d['recipient']=='user' for d in self.store.snapshot()['deliveries']))
   self.worker.tick();self.assertEqual(self.row()['quality_review']['review_count'],1);self.assertNotEqual(self.row()['status'],'completed')
   row=self.run_until_terminal();self.assertEqual(row['status'],'completed');self.assertEqual(model.call_count,2)
  self.assertEqual(self.backend.calls.count('reviewer'),2);self.assertEqual(self.backend.calls.count('developer'),1)
  self.assertEqual(row['quality_review']['recheck_count'],1);self.assertEqual(row['quality_review']['rejection_count'],0)
  final=[d for d in self.store.snapshot()['deliveries'] if d['recipient']=='user'];self.assertEqual(len(final),1);self.assertEqual(final[0]['sender'],'planner')
  report=json.loads((self.store.root/'tasks'/tid/'report.json').read_text(encoding='utf-8'));self.assertTrue(report['quality_review']['approved']);self.assertEqual(report['quality_review']['review_count'],2)
  self.assertIn('리더 검토 2회',(self.store.root/'tasks'/tid/'report.md').read_text(encoding='utf-8'))
 def test_rejection_routes_partial_instruction_and_preserves_other_files(self):
  self.store.add('answer.txt를 보완해 주세요','llama')
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',side_effect=[response('revise','결과물의 문구를 보완해야 합니다.','answer.txt 문구만 corrected로 수정하고 keep.txt는 보존하세요.'),response()]):
   row=self.run_until_terminal()
  self.assertEqual(row['status'],'completed');self.assertEqual(row['revision'],1);self.assertEqual(row['quality_review']['rejection_count'],1)
  folder=self.store.root/'tasks'/row['id']/'workspace';self.assertEqual((folder/'answer.txt').read_text(),'corrected');self.assertEqual((folder/'keep.txt').read_text(),'preserve')
  self.assertTrue(any(role=='developer' and 'answer.txt 문구만' in text for role,text in self.backend.instructions))
 def test_failed_reviewer_reports_to_leader_before_rework(self):
  tid=self.store.add('answer.txt를 정확히 작성해 주세요','llama')
  original=self.backend.run
  def fail_once(task,role,*args):
   result=original(task,role,*args)
   if role=='reviewer' and self.backend.calls.count('reviewer')==1:result.update(passed=False,summary='answer.txt의 첫 결과가 요구사항과 다릅니다.')
   return result
  with patch('app.Llama',return_value=self.backend),patch.object(self.backend,'run',side_effect=fail_once),patch('leader_review.request_json',side_effect=[response('revise','answer.txt의 내용을 수정해야 합니다.','answer.txt 문구만 corrected로 수정하세요.'),response()]):
   self.worker.tick();row=self.row();self.assertEqual(row['wait_reason'],'leader_review');self.assertEqual(row['revision'],0)
   self.assertEqual(self.backend.calls.count('developer'),1)
   with self.store.connect() as c:
    submitted=c.execute("SELECT sender,recipient,content FROM deliveries WHERE task_id=? ORDER BY created DESC LIMIT 1",(tid,)).fetchone()
    self.assertEqual(submitted['recipient'],'planner')
   row=self.run_until_terminal()
  self.assertEqual(row['status'],'completed');self.assertEqual(row['revision'],1);self.assertEqual(row['quality_review']['rejection_count'],1)
  self.assertEqual(self.backend.calls.count('developer'),2)
  with self.store.connect() as c:
   self.assertTrue(c.execute("SELECT 1 FROM messages WHERE task_id=? AND agent='planner' AND kind='handoff' AND recipient='developer'",(tid,)).fetchone())
 def test_model_cannot_approve_a_failed_teammate_verification(self):
  self.store.add('answer.txt를 작성해 주세요','llama')
  original=self.backend.run
  def fail(task,role,*args):
   result=original(task,role,*args)
   if role=='reviewer':result.update(passed=False,summary='요구 조건 검사 실패')
   return result
  with patch('app.Llama',return_value=self.backend),patch.object(self.backend,'run',side_effect=fail),patch('leader_review.request_json',return_value=response()):
   self.worker.tick();self.worker.tick()
  row=self.row();self.assertNotEqual(row['status'],'completed');self.assertFalse(row['quality_review']['approved']);self.assertEqual(row['quality_review']['review_count'],0)
 def test_rejection_budget_cannot_turn_failure_into_completion(self):
  self.store.add('answer.txt','llama',max_revisions=0)
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',return_value=response('revise','누락','누락 부분을 수정하세요.')):
   row=self.run_until_terminal()
  self.assertEqual(row['status'],'failed');self.assertEqual(row['quality_review']['rejection_count'],1)
 def test_pending_survives_restart_without_repeating_execution(self):
  self.store.add('answer.txt','llama')
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',return_value=response()):
   self.worker.tick();self.worker=Worker(Store(self.temp.name),model='qa',url='http://127.0.0.1:8766');row=self.run_until_terminal()
  self.assertEqual(row['status'],'completed');self.assertEqual(self.backend.calls.count('developer'),1)
 def test_cancellation_during_leader_model_call_prevents_approval(self):
  tid=self.store.add('answer.txt','llama')
  def cancel(*a,**kw):self.store.control('cancel',tid);return response()
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',side_effect=cancel):
   self.worker.tick();self.worker.tick()
  self.assertEqual(self.row()['status'],'cancelled');self.assertEqual(self.row()['quality_review']['review_count'],0)
 def test_files_changed_after_approval_require_new_verification(self):
  tid=self.store.add('answer.txt','llama')
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',return_value=response()):
   for _ in range(4):self.worker.tick()
   self.assertTrue(self.row()['quality_review']['approved']);(self.store.root/'tasks'/tid/'workspace'/'answer.txt').write_text('changed')
   self.worker.tick();self.assertNotEqual(self.row()['status'],'completed');self.assertIn('재검증',self.row()['error'])
 def test_false_approval_with_failed_checks_is_rejected(self):
  value=response('revise');body=json.loads(value['choices'][0]['message']['content']);body['decision']='approve';value['choices'][0]['message']['content']=json.dumps(body)
  self.store.add('answer.txt','llama')
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',return_value=value):
   self.worker.tick();self.worker.tick()
  self.assertNotEqual(self.row()['status'],'completed');self.assertEqual(self.row()['quality_review']['review_count'],0)
 def test_leader_timeout_retries_cached_submission_without_vm_replay(self):
  from model_transport import ModelError
  tid=self.store.add('answer.txt','llama',auto_recover=True)
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',side_effect=ModelError('timeout','timeout',retryable=True)),patch('app.has_vm_input',return_value=True):
   self.worker.tick();calls=list(self.backend.calls);self.worker.tick()
  self.assertEqual(self.row()['status'],'queued');self.assertEqual(self.backend.calls,calls);self.assertIsNotNone(q.pending(self.store,tid));self.assertEqual(self.row()['quality_review']['review_count'],0)
 def test_restart_during_leader_review_does_not_replay_vm_input(self):
  tid=self.store.add('answer.txt','llama',auto_recover=True)
  with patch('app.Llama',return_value=self.backend):self.worker.tick()
  with self.store.connect() as c:c.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
  with patch('app.has_vm_input',return_value=True):self.worker.recover()
  self.assertEqual(self.row()['status'],'queued');self.assertEqual(self.backend.calls.count('developer'),1)
 def test_contradictory_rejection_is_repaired_before_any_revision(self):
  bad=response();value=json.loads(bad['choices'][0]['message']['content']);value['decision']='revise';value['instructions']='통과한 내용을 다시 확인하세요.';bad['choices'][0]['message']['content']=json.dumps(value)
  self.store.add('answer.txt','llama')
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',side_effect=[bad,response(),response()]) as model:
   row=self.run_until_terminal()
  self.assertEqual(row['status'],'completed');self.assertEqual(row['revision'],0);self.assertEqual(row['quality_review']['rejection_count'],0);self.assertEqual(model.call_count,3);self.assertEqual(self.backend.calls.count('developer'),1)
 def test_guest_archive_contents_are_included_without_extracting(self):
  import io,tarfile
  tid=self.store.add('answer.txt','llama')
  with patch('app.Llama',return_value=self.backend):self.worker.tick()
  folder=self.store.root/'tasks'/tid/'workspace'
  with tarfile.open(folder/'guest-workspace.tar.gz','w:gz') as archive:
   for name,data in [('add.sh',b'echo hello\n'),('../../escape.sh',b'bad')]:
    info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
  seen=[]
  def inspect(request,**kw):
   seen.extend(json.loads(json.loads(request.data)['messages'][1]['content'])['current_files']);return response()
  from types import SimpleNamespace
  row=self.row();plan=json.loads(row['execution_plan'])
  with patch('leader_review.request_json',side_effect=inspect):q.assess(self.store,row,plan,SimpleNamespace(folder=folder),q.pending(self.store,tid),'qa','http://127.0.0.1:8766')
  self.assertTrue(any(v['path']=='guest/add.sh' and v['text']=='echo hello\n' for v in seen))
  self.assertFalse(any('escape' in v['path'] for v in seen))
 def test_repeated_contradiction_never_becomes_fake_rejection_or_approval(self):
  bad=response();value=json.loads(bad['choices'][0]['message']['content']);value['decision']='revise';value['instructions']='다시 확인';bad['choices'][0]['message']['content']=json.dumps(value)
  self.store.add('answer.txt','llama')
  with patch('app.Llama',return_value=self.backend),patch('leader_review.request_json',return_value=bad):
   self.worker.tick();self.worker.tick()
  self.assertEqual(self.row()['status'],'failed');self.assertEqual(self.row()['quality_review']['review_count'],0);self.assertEqual(self.row()['quality_review']['rejection_count'],0);self.assertEqual(self.row()['revision'],0)

if __name__=='__main__':unittest.main()
