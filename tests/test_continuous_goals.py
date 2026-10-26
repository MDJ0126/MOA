import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store
from leader_inbox import LeaderInbox
from continuous_goals import Goals
from setup_config import write_setting

class GoalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path.cwd());self.s=Store(Path(self.tmp.name).resolve())
  self.inbox=LeaderInbox(self.s,'test','http://127.0.0.1:1');self.g=Goals(self.s,self.inbox,'test','http://127.0.0.1:1')
  write_setting(self.s,'execution_profile',{'target_os':'auto','location':'host','budget_percent':50})
 def tearDown(self):self.tmp.cleanup()
 def create(self,**kw):return self.g.create({'title':'문서 정리와 실제 확인',**kw})['id']
 def proposal(self):return patch.object(self.g,'propose',return_value={'done':False,'reason':'첫 산출물 필요','task':'안내 문서를 작성하고 내용을 검증하세요.'})
 def submit(self):
  with self.proposal(),patch.object(self.inbox,'start'):self.assertTrue(self.g.step())
  return self.g.snapshot()['goals'][0]['items'][0]
 def complete(self,item,status='completed'):
  tid=self.s.add('test','demo',environment='local')
  with self.s.connect() as c:
   c.execute("UPDATE tasks SET status=?,verification=? WHERE id=?",(status,json.dumps({'passed':status=='completed'}),tid))
   c.execute("UPDATE leader_requests SET state='delegated',task_id=? WHERE id=?",(tid,item['request_id']))
  self.g.reconcile();return tid
 def test_durable_submit_idempotent_and_hold_publication(self):
  self.create();item=self.submit()
  with self.s.connect() as c:payload=json.loads(c.execute('SELECT payload FROM leader_requests').fetchone()[0])
  self.assertTrue(payload['hold_publication']);self.assertEqual(payload['environment'],'profile')
  restarted=Goals(self.s,self.inbox,'test','http://127.0.0.1:1')
  with self.proposal(),patch.object(self.inbox,'start'):self.assertFalse(restarted.step())
  self.assertEqual(restarted.snapshot()['goals'][0]['items'][0]['id'],item['id'])
 def test_next_work_continues_before_human_review(self):
  self.create();item=self.submit();self.complete(item)
  with patch.object(self.g,'propose',return_value={'done':False,'reason':'두 번째 업무','task':'두 번째 문서를 검증하세요.'}),patch.object(self.inbox,'start'):self.assertTrue(self.g.step())
  rows=self.g.snapshot()['goals'][0]['items'];self.assertEqual(len(rows),2);self.assertEqual(rows[0]['review'],'pending');self.assertEqual(rows[0]['state'],'ready')
 def test_review_approve_and_correction(self):
  ident=self.create();item=self.submit();self.complete(item)
  self.g.review({'id':item['id'],'verdict':'changes_requested','feedback':'오탈자만 고쳐 주세요.'})
  rows=self.g.snapshot()['goals'][0]['items'];self.assertEqual(rows[0]['review'],'changes_requested');self.assertEqual(rows[1]['state'],'queued');self.assertIn('오탈자',rows[1]['title'])
  with self.assertRaises(ValueError):self.g.review({'id':item['id'],'verdict':'approved'})
 def test_deadline_and_task_budget_stop_new_work(self):
  ident=self.create(max_tasks=1);item=self.submit();self.complete(item)
  self.g.step();self.assertEqual(self.g.snapshot()['goals'][0]['state'],'needs_review')
  self.g.review({'id':item['id'],'verdict':'approved'});self.assertEqual(self.g.snapshot()['goals'][0]['state'],'completed')
  with self.assertRaises(ValueError):self.g.control({'id':ident,'action':'resume'})
 def test_pause_and_stop_do_not_submit(self):
  ident=self.create();self.g.control({'id':ident,'action':'pause'})
  with self.proposal() as p:self.assertFalse(self.g.step());p.assert_not_called()
  self.g.control({'id':ident,'action':'resume'});write_setting(self.s,'paused',True)
  with self.proposal() as p:self.assertFalse(self.g.step());p.assert_not_called()
  self.g.control({'id':ident,'action':'stop'});self.assertEqual(self.g.snapshot()['goals'][0]['state'],'stopped')
 def test_expiration_during_planning_cannot_submit(self):
  ident=self.create()
  def expire(*args):
   with self.s.connect() as c:c.execute('UPDATE continuous_goals SET deadline=? WHERE id=?',(time.time()-1,ident))
   return {'done':False,'reason':'test','task':'late work'}
  with patch.object(self.g,'propose',side_effect=expire),patch.object(self.inbox,'submit') as submit:self.g.step();submit.assert_not_called()
 def test_failure_recorded_once(self):
  self.create();item=self.submit();self.complete(item,'failed');self.g.reconcile()
  self.assertEqual(self.g.snapshot()['goals'][0]['failures'],1)
 def test_invalid_configuration(self):
  for data in ({'hours':0},{'hours':float('nan')},{'max_tasks':101},{'max_tasks':True}):
   with self.assertRaises(ValueError):self.create(**data)
 def test_registration_retry_does_not_duplicate_goal(self):
  key='goal-create-retry-test';a=self.create(request_key=key);b=self.create(request_key=key)
  self.assertEqual(a,b);self.assertEqual(len(self.g.snapshot()['goals']),1)
  with self.assertRaises(ValueError):self.create(request_key=key,max_tasks=3)
 def test_windows_setup_pauses_without_burning_task_budget(self):
  self.create();write_setting(self.s,'execution_profile',{'target_os':'windows','location':'shared_vm','budget_percent':50})
  write_setting(self.s,'vm',{'enabled':True,'guest_os':'windows','iso':'','disk':'runtime/vm-images/not-created.qcow2'})
  with self.proposal() as p:self.assertTrue(self.g.step());p.assert_not_called()
  goal=self.g.snapshot()['goals'][0];self.assertEqual(goal['state'],'paused');self.assertEqual(goal['failures'],0);self.assertFalse(goal['items'])
 def test_user_pause_during_planning_is_respected(self):
  ident=self.create()
  def pause(*a):
   self.g.control({'id':ident,'action':'pause'});return {'done':False,'reason':'test','task':'late task'}
  with patch.object(self.g,'propose',side_effect=pause),patch.object(self.inbox,'submit') as submit:self.g.step();submit.assert_not_called()
 def test_stale_completed_state_cannot_be_approved(self):
  self.create();item=self.submit();tid=self.complete(item)
  with self.s.connect() as c:c.execute("UPDATE tasks SET verification='{}' WHERE id=?",(tid,))
  with self.assertRaisesRegex(ValueError,'검증 통과'):self.g.review({'id':item['id'],'verdict':'approved'})
 def test_correction_chain_can_finish_after_review(self):
  self.create();item=self.submit();self.complete(item)
  self.g.review({'id':item['id'],'verdict':'changes_requested','feedback':'오탈자 수정'})
  with patch.object(self.inbox,'start'):self.g.step()
  correction=self.g.snapshot()['goals'][0]['items'][1];self.complete(correction)
  self.g.review({'id':correction['id'],'verdict':'approved'})
  with patch.object(self.g,'propose',return_value={'done':True,'reason':'모든 결과 확인','task':''}):self.g.step()
  self.assertEqual(self.g.snapshot()['goals'][0]['state'],'completed')
 def test_correction_does_not_override_user_pause(self):
  ident=self.create(max_tasks=2);item=self.submit();self.complete(item);self.g.control({'id':ident,'action':'pause'})
  self.g.review({'id':item['id'],'verdict':'changes_requested','feedback':'부분 수정'})
  goal=self.g.snapshot()['goals'][0];self.assertEqual(goal['state'],'paused');self.assertEqual(goal['items'][1]['state'],'queued')
  self.assertEqual(self.g.control({'id':ident,'action':'resume'})['state'],'active')
 def test_transient_model_errors_retry_with_bound_and_backoff(self):
  from model_transport import ModelError
  ident=self.create();self.g.current_goal=ident
  error=ModelError('connection','temporary',retryable=True)
  self.g.record_error(error);goal=self.g.snapshot()['goals'][0];self.assertEqual(goal['state'],'active');self.assertGreater(goal['retry_at'],time.time()+25)
  with self.proposal() as p:self.assertFalse(self.g.step());p.assert_not_called()
  self.g.current_goal=ident;self.g.record_error(error);self.g.record_error(error)
  self.assertEqual(self.g.snapshot()['goals'][0]['state'],'paused')
 def test_inflight_work_keeps_awake_after_goal_deadline_or_pause(self):
  ident=self.create();self.submit();self.g.control({'id':ident,'action':'pause'})
  with self.s.connect() as c:c.execute('UPDATE continuous_goals SET deadline=?',(time.time()-1,))
  self.assertTrue(self.g.power_needed())
  write_setting(self.s,'paused',True);self.assertFalse(self.g.power_needed())
 def test_resource_install_wait_does_not_fail_or_skip_task(self):
  self.create();item=self.submit();tid=self.s.add('resource wait','demo',environment='local')
  with self.s.connect() as c:
   c.execute("UPDATE tasks SET status='interrupted',wait_reason='resource_setup' WHERE id=?",(tid,))
   c.execute("UPDATE leader_requests SET state='delegated',task_id=? WHERE id=?",(tid,item['request_id']))
  self.g.reconcile();goal=self.g.snapshot()['goals'][0];self.assertEqual(goal['items'][0]['state'],'submitted');self.assertEqual(goal['failures'],0)
  with self.proposal() as p:self.assertFalse(self.g.step());p.assert_not_called()
 def test_retried_task_rejoins_goal_and_can_finish(self):
  self.create();item=self.submit();tid=self.complete(item,'failed')
  with self.s.connect() as c:c.execute("UPDATE tasks SET status='queued' WHERE id=?",(tid,))
  self.g.reconcile();self.assertEqual(self.g.snapshot()['goals'][0]['items'][0]['state'],'submitted')
  with self.s.connect() as c:c.execute("UPDATE tasks SET status='completed',verification=? WHERE id=?",(json.dumps({'passed':True}),tid))
  self.g.reconcile();goal=self.g.snapshot()['goals'][0];self.assertEqual(goal['items'][0]['state'],'ready');self.assertEqual(goal['failures'],1)

if __name__=='__main__':unittest.main()
