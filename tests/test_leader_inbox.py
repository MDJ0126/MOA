import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store,Worker
from setup_config import PRESETS,save_team
from office_chat import OfficeChat
from leader_inbox import LeaderInbox,leader,validate
class LeaderTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup);self.s=Store(self.temp.name);__import__('setup_config').write_setting(self.s,'execution_profile',{'target_os':'auto','location':'host','budget_percent':50});save_team(self.s,PRESETS[2]);OfficeChat(self.s,'','',True);self.inbox=LeaderInbox(self.s,'model','http://127.0.0.1:0');self.agents=copy.deepcopy(PRESETS[2]['agents'])
 def submit(self,text='결과 파일을 만들어 주세요'):
  with patch.object(self.inbox,'start'):return self.inbox.submit({'title':text,'mode':'demo','request_key':'leader-test-request-1234'})
 def test_goal_holds_publication_even_when_model_requests_push(self):
  with patch.object(self.inbox,'start'):r=self.inbox.submit({'title':'결과 파일을 만들어 주세요','mode':'demo','request_key':'goal-hold-test','hold_publication':True})
  decision={**self.decision(),'git':{'publish':True,'reason':'publish','slug':'goal-result'}}
  with patch.object(self.inbox,'decide',return_value=decision):self.inbox.step()
  result=self.inbox.get(r['id']);self.assertEqual(result['state'],'delegated')
  with self.s.connect() as c:plan=json.loads(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(result['task_id'],)).fetchone()[0])
  self.assertFalse(plan['leader_workflow']['git']['publish'])
 def decision(self):return {'action':'delegate','summary':'담당자에게 구현과 검증을 분리해 요청합니다.','answer':'','assignments':[{'role':'developer','agent_id':'developer','instruction':'demo-result.md 파일을 만드세요.'},{'role':'reviewer','agent_id':'reviewer','instruction':'파일 전체 내용을 독립적으로 확인하세요.'},{'role':'reporter','agent_id':'planner','instruction':'검증 근거를 보고하세요.'}]}
 def test_selected_recipient_is_persisted_and_receives_reply_history(self):
  with patch.object(self.inbox,'start'):receipt=self.inbox.submit({'title':'안녕하세요','mode':'demo','request_key':'selected-recipient-1234','recipient_id':'reviewer'})
  self.assertEqual(receipt['leader_id'],'reviewer')
  with patch.object(self.inbox,'decide',return_value={'action':'reply','summary':'직접 답변합니다.','answer':'안녕하세요. 확인했습니다.','assignments':[]}):self.inbox.step()
  with self.s.connect() as c:
   self.assertEqual(c.execute('SELECT DISTINCT agent_id FROM office_chat').fetchone()[0],'reviewer')
 def test_request_original_and_followup_context_are_available(self):
  first=self.submit('첫 번째 원문 요청')
  self.assertEqual(self.inbox.get(first['id'])['title'],'첫 번째 원문 요청')
  self.assertEqual(self.inbox.recent()[0]['title'],'첫 번째 원문 요청')
  with patch.object(self.inbox,'start'):second=self.inbox.submit({'title':'추가 요청 내용','mode':'demo','parent_request_id':first['id'],'request_key':'followup-request-1234'})
  self.assertEqual(self.inbox.get(second['id'])['parent_request_id'],first['id'])
  with self.s.connect() as c:payload=json.loads(c.execute('SELECT payload FROM leader_requests WHERE id=?',(second['id'],)).fetchone()[0])
  self.assertEqual(payload['recent_conversation'][0]['content'],'첫 번째 원문 요청')
  self.assertEqual(second['leader_id'],first['leader_id'])
 def test_invalid_recipient_is_rejected(self):
  with patch.object(self.inbox,'start'),self.assertRaises(ValueError):self.inbox.submit({'title':'안녕하세요','mode':'demo','request_key':'invalid-recipient-1234','recipient_id':'missing'})
 def test_refinement_is_persisted_visible_and_used_in_worker_instructions(self):
  from leader_inbox import REFINEMENT_FIELDS
  refined={k:'원문에서 확인된 '+label for k,label in REFINEMENT_FIELDS.items()};refined['unknowns']='미확인 — 추가 제약은 명시되지 않았습니다.'
  receipt=self.submit('원문을 보존하고 ready.txt를 작성해 주세요')
  decision={**self.decision(),'refinement':refined}
  with patch.object(self.inbox,'decide',return_value=decision):self.inbox.step()
  result=self.inbox.get(receipt['id']);self.assertEqual(result['refinement'],refined);self.assertIn('정리',result['refinement']['changes'])
  self.assertEqual(self.inbox.recent()[0]['refinement'],refined)
  with self.s.connect() as c:
   row=c.execute('SELECT title,execution_plan FROM tasks WHERE id=?',(result['task_id'],)).fetchone();self.assertEqual(row['title'],'원문을 보존하고 ready.txt를 작성해 주세요')
   plan=json.loads(row['execution_plan']);self.assertEqual(plan['leader_workflow']['refinement'],refined)
   self.assertTrue(all('[리더가 정리한 요청]' in a['instruction'] for a in plan['leader_workflow']['assignments']))
   self.assertTrue(c.execute("SELECT 1 FROM messages WHERE task_id=? AND kind='progress' AND content LIKE '[리더가 정리한 요청]%'",(result['task_id'],)).fetchone())
 def test_refinement_and_routing_use_one_model_call(self):
  from leader_inbox import REFINEMENT_FIELDS
  self.inbox.url='http://127.0.0.1:18766'
  payload={'agents':self.agents,'title':'ready.txt를 작성해 주세요'}
  decision={**self.decision(),'refinement':{k:'명시된 원문 요구사항 '+label for k,label in REFINEMENT_FIELDS.items()}}
  response={'choices':[{'message':{'content':json.dumps(decision)},'finish_reason':'stop'}]}
  with patch('leader_inbox.request_json',return_value=response) as request:
   result=self.inbox.decide(payload);self.assertEqual(request.call_count,1);self.assertEqual(result['refinement']['goal'],decision['refinement']['goal'])
   schema=json.loads(request.call_args.args[0].data)['response_format']['json_schema']['schema']['properties'];self.assertNotIn('execute',schema['action']['enum']);self.assertNotIn('planner',schema['assignments']['items']['properties']['agent_id']['enum'])
  response['choices'][0]['message']['content']=json.dumps(self.decision())
  with patch('leader_inbox.request_json',return_value=response),self.assertRaises(ValueError):self.inbox.decide(payload)
 def test_resource_block_reason_survives_restart(self):
  tid=self.s.add('resource blocked','demo')
  with self.s.connect() as c:c.execute("UPDATE tasks SET status='interrupted',wait_reason='resource_setup',error='네트워크 연결 필요' WHERE id=?",(tid,))
  Worker(self.s).recover()
  with self.s.connect() as c:
   row=c.execute('SELECT status,wait_reason,error FROM tasks WHERE id=?',(tid,)).fetchone();self.assertEqual(tuple(row),('interrupted','resource_setup','네트워크 연결 필요'))
 def test_top_reporting_root_is_leader_even_when_not_first(self):
  self.assertEqual(leader(self.agents[1:]+self.agents[:1])['id'],'planner')
 def test_direct_reply_no_task_and_history_persists(self):
  receipt=self.submit('안녕하세요')
  with patch.object(self.inbox,'decide',return_value={'action':'reply','summary':'인사에 답합니다.','answer':'안녕하세요. 팀장입니다.','assignments':[]}):self.assertTrue(self.inbox.step())
  result=self.inbox.get(receipt['id']);self.assertEqual(result['state'],'replied');self.assertEqual(result['task_id'],'')
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0);self.assertEqual(c.execute('SELECT COUNT(*) FROM office_chat').fetchone()[0],2)
 def test_actual_task_handoff_uses_leader_assignment_and_skips_replanning(self):
  receipt=self.submit()
  with patch.object(self.inbox,'decide',return_value=self.decision()):self.inbox.step()
  result=self.inbox.get(receipt['id']);self.assertEqual(result['state'],'delegated')
  with self.s.connect() as c:
   task=dict(c.execute('SELECT * FROM tasks').fetchone());plan=json.loads(task['execution_plan']);self.assertEqual(task['stage'],1);self.assertEqual(plan['assignment']['planner'],'planner');self.assertEqual(plan['assignment']['reporter'],'planner');self.assertEqual(len(plan['leader_workflow']['assignments']),3);self.assertEqual(c.execute("SELECT recipient FROM messages WHERE kind='instruction'").fetchone()[0],'planner');self.assertEqual(c.execute("SELECT agent FROM messages WHERE kind='handoff'").fetchone()[0],'planner')
  self.assertTrue(Worker(self.s).tick())
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks').fetchone()[0],'completed')
 def test_request_replay_does_not_double_publish(self):
  first=self.submit();self.assertEqual(self.submit()['id'],first['id'])
  with patch.object(self.inbox,'decide',return_value=self.decision()):self.inbox.step();self.assertFalse(self.inbox.step())
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
 def test_replay_preserves_original_team_snapshot(self):
  receipt=self.submit()
  changed=copy.deepcopy(PRESETS[2]);changed['agents'][0]['name']='변경된 팀장';save_team(self.s,changed)
  self.assertEqual(self.submit()['id'],receipt['id'])
  self.assertNotIn('변경된',self.inbox.get(receipt['id'])['leader_name'])
 def test_invalid_assignments_and_direct_reply_with_deliverable_are_rejected(self):
  for change in [('developer','reviewer'),('reviewer','developer')]:
   value=self.decision();next(a for a in value['assignments'] if a['role']==change[0])['agent_id']=change[1]
   with self.assertRaises(ValueError):validate(self.agents,value,{})
  with self.assertRaises(ValueError):validate(self.agents,{'action':'reply','summary':'응답','answer':'완료'}, {'required_files':['result.txt']})
 def test_image_collection_is_delegated_to_subordinate_and_independent_checker(self):
  value=self.decision();value['work_kind']='image';decision=validate(self.agents,value,{})
  self.assertEqual([a['agent_id'] for a in decision['assignments'][:2]],['reporter','reviewer'])
  self.assertIn('육안 내용 판별은 지원되지',decision['assignments'][0]['instruction'])
  self.assertEqual(validate(self.agents,decision,{}),decision)
 def test_research_routes_to_actual_specialty(self):
  self.agents[2]['specialty']='시장 조사 · 리서치';self.agents[2]['work_kinds']=['research','image'];value=self.decision();value['work_kind']='research'
  decision=validate(self.agents,value,{})
  self.assertEqual(decision['assignments'][0]['agent_id'],self.agents[2]['id'])
 def test_leader_self_execution_proposal_is_delegated_without_mutating_saved_team(self):
  decision=validate(self.agents,{'action':'execute','work_kind':'image','summary':'직접 확보합니다.','assignments':[]},{'title':'해바라기 사진을 구해 주세요'})
  tid=self.s.add('해바라기 사진을 구해 주세요','demo',leader_decision={**decision,'agents':self.agents,'leader_id':'planner','request_id':'self-test'})
  with self.s.connect() as c:
   row=c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone();plan=json.loads(row[0]);self.assertEqual(plan['stage_labels']['developer'],'이미지 확보');self.assertEqual(plan['assignment']['developer'],'reporter')
   self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='handoff' AND agent=recipient",(tid,)).fetchone()[0],0)
  self.assertTrue(Worker(self.s).tick())
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'completed')
  from setup_config import team_settings
  self.assertNotIn('developer',team_settings(self.s)['agents'][0]['capabilities'])
 def test_lone_agent_can_execute_but_manager_does_not_take_missing_duty(self):
  only=copy.deepcopy(PRESETS[0]['agents'])
  value={'action':'execute','work_kind':'image','summary':'확보합니다.','assignments':[]}
  actual=validate(only,value,{'title':'사진 한 장'})
  self.assertTrue(actual['self_execution'])
  agents=copy.deepcopy(self.agents)
  for a in agents[1:]:a['work_kinds']=['code']
  with self.assertRaisesRegex(ValueError,'하위 담당자'):validate(agents,value,{'title':'사진 한 장'})
 def test_explicit_leaf_recipient_keeps_own_work(self):
  value={'action':'execute','work_kind':'code','summary':'구현합니다.','assignments':[]}
  actual=validate(self.agents,value,{'recipient_id':'developer','title':'파일을 구현하세요'})
  self.assertEqual(actual['assignments'][0]['agent_id'],'developer')
 def test_other_branch_is_not_used_by_subleader(self):
  from leader_inbox import reports_to
  agents=[{'id':'root'},{'id':'manager','manager_id':'root'},{'id':'child','manager_id':'manager'},{'id':'other','manager_id':'root'}]
  self.assertEqual([a['id'] for a in reports_to(agents,agents[1])],['child'])
 def test_code_still_requires_independent_reviewer(self):
  value=self.decision();value['assignments'][1]['agent_id']='developer'
  with self.assertRaises(ValueError):validate(self.agents,value,{})
if __name__=='__main__':unittest.main()
