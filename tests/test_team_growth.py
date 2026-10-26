import json,tempfile,unittest
from pathlib import Path
from app import Store,checked_plan
from setup_config import PRESETS,save_team
import team_growth as g
import peer_scores as p
class GrowthTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup);self.s=Store(self.temp.name);save_team(self.s,PRESETS[1])
 def task(self,title,success=True,reject=False):
  tid=self.s.add(title,'llama')
  with self.s.connect() as c:task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
  plan=checked_plan(task);g.bind(self.s,task,plan)
  self.s.log(tid,'builder','guest_result',json.dumps({'command':'test actual fixture','exit_code':0 if success else 1,'output':'ok' if success else 'assertion failed'}),capability='developer')
  self.s.log(tid,'builder','handoff',json.dumps({'summary':'직접 실행한 결과와 검증 근거를 다음 담당자에게 전달합니다.','guest_results':[{'exit_code':0 if success else 1}]}),capability='developer')
  self.s.log(tid,'checker','handoff',json.dumps({'summary':'검증 결과를 독립적으로 확인하여 리더에게 전달합니다.','guest_results':[{'exit_code':0 if success else 1}]}),capability='reviewer')
  if reject:self.s.log(tid,'checker','review_rejection','요청한 경계 조건 누락이 실제 검증 실패로 확인되어 부분 수정을 요청합니다.',capability='reviewer')
  with self.s.connect() as c:c.execute("UPDATE tasks SET status=?,verification=? WHERE id=?",('completed' if success else 'failed',json.dumps({'passed':success,'tests_run':1}),tid));return tid,g.assess(c,tid)
 def test_failure_then_verified_improvement_then_normal_is_neutral(self):
  first,a=self.task('first comparable',False,True);self.assertLess(a['builder']['points'],0)
  second,b=self.task('second comparable',True);self.assertGreater(b['builder']['points'],0);self.assertEqual(b['builder']['comparison_task_id'],first)
  third,c=self.task('third comparable',True);self.assertEqual(c['builder']['points'],0)
  self.assertIn('리더 개인 개선 목표',g.instructions(self.s,second,'builder'))
 def test_first_success_is_zero_and_goal_is_persisted_per_actor(self):
  tid,v=self.task('first success');self.assertEqual(v['builder']['points'],0);self.assertEqual(len(g.detail(self.s,'builder')),1)
  with self.s.connect() as c:t=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
  g.bind(self.s,t,checked_plan(t));self.assertEqual(len(g.detail(self.s,'builder')),1)
 def test_schema_limits_target_and_evidence_and_affinity_cannot_reward_work(self):
  tid,_=self.task('normal success');p.enqueue(self.s,tid)
  with self.s.connect() as c:payload=json.loads(c.execute('SELECT payload FROM peer_score_jobs WHERE task_id=? LIMIT 1',(tid,)).fetchone()[0])
  target=payload['targets'][0]['id'];seq=next(r['seq'] for r in payload['evidence'] if r['agent']==target)
  value={'target':target,'points':3,'basis':'exceptional','observed_difference':'','reason':'정상 업무 완료와 실행 성공 근거만 확인되므로 특별한 관계 개선이라고 인정할 수 없습니다.','evidence_seqs':[seq]}
  self.assertEqual(p.validate(payload,{'ratings':[value]})[0]['points'],0)
  schema=p.rating_schema(target,[seq]);self.assertEqual(schema['properties']['ratings']['items']['properties']['target']['enum'],[target])
 def test_legacy_inflation_is_archived_and_requeued_without_data_loss(self):
  tid,_=self.task('legacy');p.enqueue(self.s,tid)
  with self.s.connect() as c:c.execute("INSERT INTO peer_scores(task_id,rater,target,points,reason,evidence) VALUES(?,?,?,?,?,?)",(tid,'checker','builder',2,'legacy positive','[]'))
  self.assertEqual(p.summary(self.s)['builder']['total'],52)
  p.PeerScores(self.s,'test','http://localhost').recover()
  self.assertEqual(p.summary(self.s)['builder']['total'],50)
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM score_recalibration_archive').fetchone()[0],1);self.assertEqual(c.execute('SELECT superseded FROM peer_scores').fetchone()[0],1)
