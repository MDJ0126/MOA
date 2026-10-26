import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app import Store
from setup_config import PRESETS,save_team,read_setting
from slack_service import SlackService
from slack_conversations import SlackConversations
class Web:
 def __init__(self):self.posts=[];self.fail=False
 def chat_postMessage(self,**kw):
  self.posts.append({**kw,'ts':'700.'+str(len(self.posts))})
  if self.fail:raise ConnectionError('lost response')
  return {'ts':self.posts[-1]['ts']}
 def conversations_replies(self,**kw):return {'messages':self.posts}
class SlackConversationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.tmp.cleanup);self.s=Store(self.tmp.name);save_team(self.s,PRESETS[2]);self.service=SlackService(self.s)
  self.agents=copy.deepcopy(PRESETS[2]['agents']);self.connections=[{'id':a['id'],'agent_id':a['id']} for a in self.agents]
  self.service.config={'enabled':True,'receive':True,'notify':True,'collaboration':False,'channel':'C12345678','allowed_users':['U12345678'],'connections':self.connections}
  self.webs={c['id']:Web() for c in self.connections};self.service.web=self.webs;self.service.bot_user_ids={c['id']:'UBOT'+str(i) for i,c in enumerate(self.connections)};self.decider=Mock();self.router=SlackConversations(self.service,self.decider);self.service.conversations=self.router;self.connection=next(c for c in self.connections if c['agent_id']=='planner')
 def event(self,text='여러분',identity='Event1',ts='500.1',**fields):return {'event_id':identity,'event':{'type':'message','channel_type':'channel','channel':'C12345678','user':'U12345678','text':text,'ts':ts,**fields}}
 def task_count(self):
  with self.s.connect() as c:return c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
 def test_unmentioned_channel_greeting_replies_once_with_four_socket_deliveries(self):
  for i,c in enumerate(reversed(self.connections)):self.service.receive(self.event(identity='Event'+str(i)),c)
  posts=[p for w in self.webs.values() for p in w.posts];self.assertEqual(len(posts),1);self.assertIn('말씀하세요',posts[0]['text']);self.assertTrue(posts[0]['reply_broadcast']);self.assertEqual(self.task_count(),0);self.decider.decide.assert_not_called()
 def test_duplicate_message_and_mention_event_do_not_double_reply(self):
  self.service.receive(self.event(),self.connection);p=self.event(identity='OtherAppEvent');p['event']['type']='app_mention';self.service.receive(p,self.connection);self.assertEqual(len(self.webs['planner'].posts),1)
 def test_foreign_bot_unauthorized_user_and_other_channel_get_no_reply(self):
  for i,changes in enumerate([{'bot_id':'BFORIEGN'},{'user':'UOTHER'},{'channel':'COTHER'}]):self.service.receive(self.event(identity='Rejected'+str(i),**changes),self.connection)
  self.assertFalse(any(w.posts for w in self.webs.values()));self.assertEqual(self.task_count(),0)
 def test_explicit_bot_mention_is_answered_by_that_bot(self):
  target=next(c for c in self.connections if c['agent_id']=='developer');text='<@'+self.service.bot_user_ids[target['id']]+'> 안녕하세요'
  self.service.receive(self.event(text),self.connection);self.service.receive(self.event(text,identity='DevEvent'),target);self.assertEqual(len(self.webs[target['id']].posts),1);self.assertEqual(len(self.webs['planner'].posts),0)
 def test_direct_message_greeting_replies_without_broadcast(self):
  target=next(c for c in self.connections if c['agent_id']=='developer');self.service.receive(self.event('안녕하세요',channel='D12345678',channel_type='im'),target);self.assertFalse(self.webs[target['id']].posts[0]['reply_broadcast'])
 def test_question_gets_immediate_ack_then_answer_without_vm_task(self):
  self.decider.decide.return_value={'action':'reply','summary':'질문 응답','answer':'확인한 내용을 설명드리겠습니다.','assignments':[]}
  self.service.receive(self.event('어떤 업무를 할 수 있나요?'),self.connection);self.assertEqual(len(self.webs['planner'].posts),1);self.assertIn('말씀 확인',self.webs['planner'].posts[0]['text']);self.assertTrue(self.router.step());self.assertEqual(len(self.webs['planner'].posts),2);self.assertEqual(self.task_count(),0)
 def test_delegate_preserves_channel_thread_requester_and_named_assignments(self):
  self.decider.decide.return_value={'action':'delegate','summary':'담당자에게 전달합니다.','answer':'','assignments':[{'role':'developer','agent_id':'developer','instruction':'결과 파일을 만드세요.'},{'role':'reviewer','agent_id':'reviewer','instruction':'독립 검증하세요.'}]}
  self.service.receive(self.event('보고서 파일을 만들어 주세요'),self.connection);self.router.step();self.assertEqual(self.task_count(),1)
  with self.s.connect() as c:
   t=dict(c.execute('SELECT * FROM tasks').fetchone());thread=c.execute('SELECT * FROM slack_threads WHERE task_id=?',(t['id'],)).fetchall();self.assertEqual(len(thread),1);self.assertEqual(thread[0]['thread_ts'],'500.1');self.assertEqual(c.execute('SELECT user_id FROM slack_requesters WHERE task_id=?',(t['id'],)).fetchone()[0],'U12345678')
  self.assertEqual(json.loads(t['execution_plan'])['assignment']['developer'],'developer');self.assertIn('업무를 접수',self.webs['planner'].posts[-1]['text']);self.assertFalse(self.router.step())
 def test_status_and_paused_greeting_work_while_other_job_is_active(self):
  tid=self.s.add('현재 업무','demo');self.s.control('pause');self.service.receive(self.event('여러분'),self.connection);self.service.receive(self.event('진행 상황',identity='Status2',ts='500.2'),self.connection);self.assertIn('현재 업무',self.webs['planner'].posts[-1]['text']);self.assertEqual(self.task_count(),1)
 def test_lost_reply_response_is_reconciled_without_duplicate_post(self):
  web=self.webs['planner'];web.fail=True
  with self.assertRaises(ConnectionError):self.service.receive(self.event(),self.connection)
  web.fail=False;self.service.receive(self.event(),self.connection);self.assertEqual(len(web.posts),1)
 def test_human_thread_instruction_targets_current_assignee_not_replanning(self):
  tid=self.s.add('진행할 일','demo',slack_context={'channel':'C12345678','thread_ts':'500.1','connection_id':'planner','agent_id':'shared','user_id':'U12345678'})
  with self.s.connect() as c:c.execute('UPDATE tasks SET stage=1 WHERE id=?',(tid,))
  self.service.receive(self.event('표 색상을 바꿔 주세요',thread_ts='500.1'),self.connection);self.assertIn('추가 지시',self.webs['planner'].posts[-1]['text']);self.assertEqual(self.task_count(),1)
 def test_control_side_effect_is_durable_before_reply(self):
  web=self.webs['planner'];web.fail=True
  with self.assertRaises(ConnectionError):self.service.receive(self.event('일시 중지'),self.connection)
  self.assertTrue(read_setting(self.s,'paused',False));web.fail=False;self.service.receive(self.event('일시 중지'),self.connection);self.assertEqual(len(web.posts),1)

 def test_question_failure_is_reported_and_backed_off(self):
  self.decider.decide.side_effect=ValueError('모델 응답 확인 필요');self.service.receive(self.event('어떤 도구가 있나요?'),self.connection);self.router.step();self.assertIn('문제가 발생',self.webs['planner'].posts[-1]['text']);self.assertFalse(self.router.step())
 def test_recent_replies_are_available_for_followup(self):
  self.decider.decide.return_value={'action':'reply','summary':'답변','answer':'질문에 대한 답변입니다.','assignments':[]};self.service.receive(self.event('무엇을 할 수 있나요?'),self.connection);self.router.step();self.service.receive(self.event('그중 첫 번째는요?',identity='Followup',ts='500.2',thread_ts='500.1'),self.connection);self.router.step();self.assertEqual(self.decider.decide.call_args.args[0]['recent_conversation'][0]['answer'],'질문에 대한 답변입니다.')
 def test_instruction_queued_during_planning_routes_to_new_active_task(self):
  self.service.receive(self.event('표 색상을 바꿔 주세요',thread_ts='500.1'),self.connection)
  tid=self.s.add('진행할 일','demo',slack_context={'channel':'C12345678','thread_ts':'500.1','connection_id':'planner','agent_id':'shared','user_id':'U12345678'})
  with self.s.connect() as c:c.execute('UPDATE tasks SET stage=1 WHERE id=?',(tid,))
  self.router.step();self.decider.decide.assert_not_called();self.assertEqual(self.task_count(),1);self.assertIn('추가 지시',self.webs['planner'].posts[-1]['text'])
 def test_restored_conversation_needs_explicit_recovery(self):
  self.service.receive(self.event('보고서 작성해 주세요'),self.connection)
  with self.s.connect() as c:
   row=c.execute('SELECT id FROM slack_conversations').fetchone();c.execute("UPDATE slack_conversations SET state='held_restored' WHERE id=?",(row[0],))
  self.assertFalse(self.router.step());snapshot=self.service.recovery();self.assertEqual(snapshot['counts']['conversation'],1);self.service.recover('resume',[{'kind':'conversation','id':row[0]}])
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT state FROM slack_conversations').fetchone()[0],'received')

if __name__=='__main__':unittest.main()
