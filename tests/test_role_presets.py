import copy,tempfile,unittest
from pathlib import Path
from app import Store
from setup_config import PRESETS,save_team,team_settings,validate_team
from leader_inbox import validate
from organization import instruction
import role_presets as r
class RolePresetTests(unittest.TestCase):
 def test_search_aliases_and_related_roles(self):
  self.assertGreaterEqual(len(r.CATALOG),20)
  self.assertIn('frontend',{p['id'] for p in r.search('REACT')})
  self.assertTrue({'unity','unreal','game-design'}<={p['id'] for p in r.search('게임')})
  self.assertIn('research',{p['id'] for p in r.search('사진')})
  self.assertEqual(r.search('존재하지않는직무1234'),[])
  results=r.search('유니티');results[0]['work_kinds'].clear();self.assertTrue(r.search('유니티')[0]['work_kinds'])
 def test_custom_scope_survives_save_reload_and_frozen_task(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as d:
   s=Store(d);v=copy.deepcopy(PRESETS[2]);a=v['agents'][1];a.update(role_preset='research',specialty='사용자 연구원',responsibilities='공식 출처 조사만 직접 수행',boundaries='코드 구현은 개발 담당에게 요청',work_kinds=['research','image'])
   saved=save_team(s,v);actual=team_settings(s)['agents'][1]
   for k in ('role_preset','specialty','responsibilities','boundaries','work_kinds'):self.assertEqual(actual[k],a[k])
   tid=s.add('문서 조사','demo')
   with s.connect() as c:
    import json
    frozen=json.loads(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0])
   self.assertEqual(frozen['agents'][1]['boundaries'],a['boundaries']);self.assertIn(a['boundaries'],instruction(frozen['agents']))
 def test_disallowed_scope_reroutes_to_specialist_and_no_owner_fails(self):
  agents=copy.deepcopy(PRESETS[2]['agents']);agents[3].update(work_kinds=['image','research'],specialty='리서처',job_title='리서처')
  decision={'action':'delegate','work_kind':'research','summary':'조사를 맡깁니다.','assignments':[{'role':'developer','agent_id':agents[1]['id'],'instruction':'출처를 조사하세요.'},{'role':'reviewer','agent_id':agents[2]['id'],'instruction':'본문과 출처를 확인하세요.'}]}
  self.assertEqual(validate(agents,decision,{})['assignments'][0]['agent_id'],agents[3]['id'])
  for a in agents:a['work_kinds']=['code']
  with self.assertRaisesRegex(ValueError,'담당할 수 있는 역할'):validate(agents,decision,{})
 def test_one_specialist_keeps_real_verification_and_code_stays_strict(self):
  agents=copy.deepcopy(PRESETS[2]['agents']);agents[1].update(role_preset='research',job_title='리서처',specialty='리서처',work_kinds=['research','image'])
  value={'action':'delegate','work_kind':'research','summary':'리서처에게 맡깁니다.','assignments':[{'role':'developer','agent_id':agents[1]['id'],'instruction':'출처를 조사하세요.'}]}
  actual=validate(agents,value,{});self.assertEqual(len(actual['assignments']),2);self.assertEqual(actual['assignments'][1]['role'],'reviewer');self.assertEqual(actual['assignments'][0]['agent_id'],agents[1]['id'])
  value['work_kind']='code'
  with self.assertRaises(ValueError):validate(agents,value,{})
 def test_invalid_scope_and_metadata_are_rejected(self):
  for value in ([],['unknown'],'research',[1]):
   team=copy.deepcopy(PRESETS[2]);team['agents'][0]['work_kinds']=value
   with self.assertRaises(ValueError):validate_team(team)
  team=copy.deepcopy(PRESETS[2]);team['agents'][0]['responsibilities']=123
  with self.assertRaises(ValueError):validate_team(team)
 def test_default_enrichment_preserves_user_fields(self):
  original={'id':'member','name':'이름','capabilities':['developer'],'instructions':'내 규칙','job_title':'개발','specialty':'내 전문 분야'};a=r.enrich(original)
  for k,v in original.items():self.assertEqual(a[k],v)
  a['responsibilities']='';a['work_kinds']=['image'];self.assertEqual(r.enrich(a),a)
  self.assertEqual(r.enrich({'capabilities':list(('planner','developer','reviewer','reporter'))})['work_kinds'],list(r.KINDS))
if __name__=='__main__':unittest.main()
