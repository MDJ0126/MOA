import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store
from setup_config import PRESETS,save_team,write_setting
from office_chat import OfficeChat
from leader_inbox import LeaderInbox,validate,REFINEMENT_FIELDS
import routing_cache as cache

class RoutingCacheTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup)
  self.s=Store(self.temp.name);write_setting(self.s,'execution_profile',{'target_os':'auto','location':'host','budget_percent':50});save_team(self.s,PRESETS[2]);OfficeChat(self.s,'','',True)
  self.inbox=LeaderInbox(self.s,'model','http://127.0.0.1:0')
  self.payload={'title':'Implement result.sh and independently check it','required_files':['result.sh'],'agents':copy.deepcopy(PRESETS[2]['agents']),'workspace':{'global_instructions':'Full user policy','skills':[]}}
  self.decision=validate(self.payload['agents'],{'action':'execute','work_kind':'code','summary':'구현과 독립 검증','assignments':[],'refinement':{k:('원문 구현과 검증' if k=='goal' else '') for k in REFINEMENT_FIELDS}},self.payload)
  self.tid=self.s.add('successful original','demo')
 def complete(self,passed=True):
  with self.s.connect() as c:c.execute("UPDATE tasks SET status='completed',verification=? WHERE id=?",(json.dumps({'passed':passed}),self.tid))
 def test_key_invalidates_original_policy_team_model_and_config(self):
  original=cache.key(self.payload,'m','http://local')
  for field,value in [('title','different'),('required_files',['other.sh']),('workspace',{'global_instructions':'Changed policy'}),('agents',self.payload['agents'][:-1])]:
   altered={**self.payload,field:value};self.assertNotEqual(original,cache.key(altered,'m','http://local'))
  self.assertNotEqual(original,cache.key(self.payload,'new','http://local'))
  self.assertNotEqual(original,cache.key(self.payload,'m','http://elsewhere'))
  with patch('routing_cache.Path.read_text',return_value='changed config'):self.assertNotEqual(original,cache.key(self.payload,'m','http://local'))
  self.assertIsNone(cache.key({**self.payload,'recent_conversation':[{'content':'context'}]},'m','http://local'))
 def test_only_successful_verified_work_can_be_reused_and_expires(self):
  key=cache.key(self.payload,'m','http://local');cache.put(self.s,key,self.decision,self.tid)
  self.assertIsNone(cache.get(self.s,key));self.complete(False);self.assertIsNone(cache.get(self.s,key))
  self.complete();decision,source=cache.get(self.s,key);self.assertEqual(source,self.tid);self.assertEqual(decision,self.decision)
  with patch('routing_cache.time.time',return_value=__import__('time').time()+cache.TTL_SECONDS+1):self.assertIsNone(cache.get(self.s,key))
 def test_cache_hit_still_publishes_a_new_real_task_without_model(self):
  with patch.object(self.inbox,'start'):request=self.inbox.submit({'title':self.payload['title'],'required_files':['result.sh'],'mode':'llama','request_key':'cache-integration-123'})
  with self.s.connect() as c:payload=json.loads(c.execute('SELECT payload FROM leader_requests WHERE id=?',(request['id'],)).fetchone()[0])
  key=cache.key(payload,self.inbox.model,self.inbox.url);cache.put(self.s,key,self.decision,self.tid);self.complete()
  with patch.object(self.inbox,'decide',side_effect=AssertionError('Cache must avoid inference')):self.assertTrue(self.inbox.step())
  result=self.inbox.get(request['id']);self.assertNotEqual(result['task_id'],self.tid);self.assertTrue(result['decision']['routing_cache']['hit'])
  with self.s.connect() as c:
   task=c.execute('SELECT status,title FROM tasks WHERE id=?',(result['task_id'],)).fetchone();self.assertEqual(task['status'],'queued');self.assertEqual(task['title'],payload['title'])
