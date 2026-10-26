import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store,Worker,Llama,Demo
from setup_config import PRESETS,save_team
import peer_scores as ps

class PeerScoreTests(unittest.TestCase):
 def setUp(self):
  grace=patch('peer_scores.IDLE_GRACE_SECONDS',0);grace.start();self.addCleanup(grace.stop)
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
  self.s=Store(self.temp.name);save_team(self.s,PRESETS[1]);self.tid=self.s.add('상호 평가 실제 수행 기록 검증','llama')
  for aid in ['builder','checker']:self.s.log(self.tid,aid,'handoff',json.dumps({'summary':aid+' 수행 기록','passed':True}),capability='developer' if aid=='builder' else 'reviewer')
  with self.s.connect() as c:c.execute("UPDATE tasks SET status='completed' WHERE id=?",(self.tid,))
  self.worker=ps.PeerScores(self.s,'model','http://127.0.0.1:8766')
 def reply(self,request,timeout):
  payload=json.loads(json.loads(request.data)['messages'][1]['content']);ratings=[]
  for target in payload['targets']:
   seq=next(r['seq'] for r in payload['evidence'] if r['agent']==target['id'])
   ratings.append({'target':target['id'],'points':3 if target['id']=='builder' else -3,'basis':'exceptional' if target['id']=='builder' else 'critical_issue','observed_difference':'일반 기대 수준과 실제 수행 결과의 차이가 fixture에 명시되어 있습니다.','reason':'업무 기록에서 담당자의 수행 내용과 검증 결과가 확인됩니다. 이 근거의 기여도와 한계를 함께 고려해 점수를 정했습니다.','evidence_seqs':[seq]})
  return {'choices':[{'message':{'content':json.dumps({'ratings':ratings})},'finish_reason':'stop'}]}
 def payload(self):
  ps.enqueue(self.s,self.tid)
  with self.s.connect() as c:return json.loads(c.execute('SELECT payload FROM peer_score_jobs LIMIT 1').fetchone()[0])
 def test_mutual_extremes_persist_and_replay_never_adds_twice(self):
  self.assertEqual(ps.enqueue(self.s,self.tid),2);self.assertEqual(ps.enqueue(self.s,self.tid),0)
  with patch('peer_scores.request_json',side_effect=self.reply):
   self.assertTrue(self.worker.step());self.assertTrue(self.worker.step());self.assertFalse(self.worker.step())
  self.assertEqual(ps.summary(self.s)['builder'],{'total':50,'count':1});self.assertEqual(ps.summary(self.s)['checker'],{'total':50,'count':1})
  log=ps.detail(Store(self.temp.name),'builder');self.assertEqual(len(log['logs']),2);self.assertTrue(log['logs'][0]['evidence']);self.assertEqual(log['jobs'],[])
  with self.s.connect() as c:c.execute("UPDATE peer_score_jobs SET state='queued'")
  with patch('peer_scores.request_json',side_effect=self.reply):self.worker.step();self.worker.step()
  self.assertEqual(ps.summary(self.s)['builder']['total'],50)
 def test_removed_affinity_never_enqueues_or_calls_model(self):
  ps.enqueue(self.s,self.tid)
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM affinity_score_jobs').fetchone()[0],0)
  archived=ps.PeerScores(self.s,'model','http://localhost','affinity')
  with patch('peer_scores.request_json') as model:self.assertFalse(archived.step());model.assert_not_called()
 def test_work_starts_at_50_without_upper_limit(self):
  self.assertEqual(ps.summary(self.s)['builder']['total'],50)
  with self.s.connect() as c:
   for i in range(25):c.execute('INSERT INTO peer_scores(task_id,rater,target,points,reason,evidence) VALUES(?,?,?,?,?,?)',('fixture'+str(i),'checker','builder',3,'fixture','[]'))
  self.assertEqual(ps.summary(self.s)['builder']['total'],125)

 def test_invalid_points_self_rating_and_fake_evidence_rejected(self):
  payload=self.payload();target=payload['targets'][0]['id'];seq=next(r['seq'] for r in payload['evidence'] if r['agent']==target)
  good={'target':target,'points':0,'reason':'기록에 있는 업무 내용만 확인했으며 추가 성과를 주장할 근거는 부족합니다. 따라서 중립 점수를 적용합니다.','evidence_seqs':[seq]}
  self.assertEqual(ps.validate({**payload,'policy_version':1},{'ratings':[good]})[0]['points'],0)
  self.assertEqual(ps.validate(payload,{'ratings':[{**good,'basis':'ordinary'}]})[0]['points'],0)
  with self.assertRaises(ValueError):ps.validate({**payload,'policy_version':2},{'ratings':[{**good,'points':2,'basis':'ordinary','observed_difference':'통상적인 업무 완료이며 그 밖의 개선 근거는 제공되지 않았습니다.'}]})
  for change in [{'points':4},{'points':-4},{'points':True},{'points':1.5},{'target':payload['rater']['id']},{'evidence_seqs':[99999]},{'reason':'짧음'},{'evidence_seqs':[]}]:
   with self.subTest(change=change),self.assertRaises(ValueError):ps.validate(payload,{'ratings':[{**good,**change}]})
 def test_failure_does_not_change_score_and_shows_error(self):
  ps.enqueue(self.s,self.tid)
  with patch('peer_scores.request_json',side_effect=RuntimeError('secret-token')):self.worker.step()
  self.assertEqual(ps.summary(self.s)['builder']['total'],50);self.assertTrue(any(j['state']=='queued' for j in ps.detail(self.s,'builder')['jobs']));self.assertNotIn('secret-token',str(ps.detail(self.s,'builder')))
 def test_demo_and_single_participant_do_not_fabricate_scores(self):
  with self.s.connect() as c:c.execute("UPDATE tasks SET mode='demo' WHERE id=?",(self.tid,))
  self.assertEqual(ps.enqueue(self.s,self.tid),0)
  with self.s.connect() as c:c.execute("UPDATE tasks SET mode='llama' WHERE id=?",(self.tid,));c.execute("DELETE FROM messages WHERE agent='checker'")
  self.assertEqual(ps.enqueue(self.s,self.tid),0)
 def test_worker_completion_automatically_enqueues_both_peers(self):
  tid=self.s.add('자동 평가 연결 fixture','llama')
  with patch('app.leader_review.policy',return_value=False),patch.object(Llama,'run',Demo.run),patch('app.run_checks',return_value={'passed':True,'tests_run':1,'scope':'test fixture'}):Worker(self.s).tick()
  with self.s.connect() as c:
   self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'completed')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM peer_score_jobs WHERE task_id=?',(tid,)).fetchone()[0],2)

 def test_final_failure_and_rework_context_are_included(self):
  self.s.log(self.tid,'system','error','검증에서 중요한 누락이 확인되어 재작업했습니다.')
  with self.s.connect() as c:c.execute("UPDATE tasks SET status='failed' WHERE id=?",(self.tid,))
  self.assertEqual(ps.enqueue(self.s,self.tid),2)
  with self.s.connect() as c:payload=json.loads(c.execute('SELECT payload FROM peer_score_jobs LIMIT 1').fetchone()[0])
  self.assertEqual(payload['task_status'],'failed');self.assertEqual(payload['policy_version'],3)
  self.assertTrue(payload['failure_context'])
 def test_routine_completion_is_neutral_for_both_kinds(self):
  payload=self.payload();target=payload['targets'][0]['id'];seq=next(r['seq'] for r in payload['evidence'] if r['agent']==target)
  rating={'target':target,'points':0,'basis':'ordinary','reason':'요구된 정상 업무를 수행한 기록만 있으므로 추가적인 개선이나 문제를 확인할 수 없어 중립으로 평가합니다.','evidence_seqs':[seq]}
  self.assertEqual(ps.validate(payload,{'ratings':[rating]})[0]['points'],0)
  self.assertEqual(ps.validate(payload,{'ratings':[{**rating,'points':2}]})[0]['points'],0)
 def test_active_work_preempts_evaluation_and_restart_recovers(self):
  ps.enqueue(self.s,self.tid)
  self.s.control('pause')
  with patch('peer_scores.request_json') as model:self.assertFalse(self.worker.step());model.assert_not_called()
  self.s.control('resume')
  with self.s.connect() as c:c.execute("UPDATE tasks SET status='running' WHERE id=?",(self.tid,))
  with patch('peer_scores.request_json') as model:self.assertFalse(self.worker.step());model.assert_not_called()
  with self.s.connect() as c:c.execute("UPDATE peer_score_jobs SET state='running'")
  self.worker.recover()
  self.assertTrue(all(j['state']=='queued' for j in ps.detail(self.s,'builder')['jobs']))
 def test_user_requests_preempt_background_scores_before_task_publication(self):
  ps.enqueue(self.s,self.tid)
  for table,definition,values in (
   ('leader_requests','state TEXT',"'queued'"),
   ('continuous_goals','state TEXT',"'active'"),
   ('slack_conversations','state TEXT',"'planning'"),
   ('office_chat','role TEXT,state TEXT',"'assistant','queued'")):
   with self.subTest(table=table):
    with self.s.connect() as c:
     c.execute('CREATE TABLE IF NOT EXISTS '+table+'('+definition+')');c.execute('INSERT INTO '+table+' VALUES('+values+')')
    with patch('peer_scores.request_json') as model:self.assertFalse(self.worker.step());model.assert_not_called()
    with self.s.connect() as c:c.execute('DELETE FROM '+table)


 def test_background_ratings_wait_for_sustained_idle(self):
  ps.enqueue(self.s,self.tid)
  worker=ps.PeerScores(self.s,'model','http://127.0.0.1:8766',idle_seconds=60)
  with patch('peer_scores.request_background_json',side_effect=lambda req,timeout,check:self.reply(req,timeout)) as model:
   self.assertFalse(worker.step());model.assert_not_called()
   worker._idle_since-=61
   self.assertFalse(worker.step());model.assert_not_called()
   with self.s.connect() as c:c.execute('UPDATE task_timing_events SET recorded=recorded-61')
   self.assertTrue(worker.step());self.assertEqual(model.call_count,1)



 def test_interrupted_assessment_is_queued_without_scores(self):
  ps.enqueue(self.s,self.tid)
  worker=ps.PeerScores(self.s,'model','http://127.0.0.1:8766',idle_seconds=60);worker._idle_since-=61
  with self.s.connect() as c:c.execute('UPDATE task_timing_events SET recorded=recorded-61')
  with patch('peer_scores.request_background_json',side_effect=ps.BackgroundYield('foreground')):
   self.assertTrue(worker.step())
  with self.s.connect() as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM peer_scores').fetchone()[0],0)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM peer_score_jobs WHERE state='queued'").fetchone()[0],2)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM peer_score_jobs WHERE state='failed'").fetchone()[0],0)


 def test_bad_schema_is_corrected_before_job_is_failed(self):
  ps.enqueue(self.s,self.tid);calls=[]
  def respond(request,timeout):
   calls.append(1)
   if len(calls)==1:return {'choices':[{'message':{'content':'{"ratings":[]}'},'finish_reason':'stop'}]}
   return self.reply(request,timeout)
  with patch('peer_scores.request_json',side_effect=respond):self.worker.step()
  self.assertEqual(len(calls),2)
  with self.s.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM peer_score_jobs WHERE state='completed'").fetchone()[0],1)
 def test_connection_failure_retries_then_completes_without_duplicate_scores(self):
  ps.enqueue(self.s,self.tid)
  with patch('peer_scores.request_json',side_effect=ps.ModelError('connection','fixture',retryable=True)):self.worker.step()
  with self.s.connect() as c:
   row=c.execute('SELECT state,attempts FROM peer_score_jobs WHERE attempts=1').fetchone();self.assertEqual(tuple(row),('queued',1));c.execute('UPDATE peer_score_jobs SET retry_at=0')
  with patch('peer_scores.request_json',side_effect=self.reply):self.worker.step()
  with self.s.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM peer_scores WHERE policy_version=3").fetchone()[0],1)

if __name__=='__main__':unittest.main()
