import copy,unittest,tempfile
from pathlib import Path
from app import Store
from setup_config import PRESETS,save_team,team_settings
import organization as org
from workspace_resources import frozen_context
class OrganizationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup);self.store=Store(self.temp.name)
 def test_titles_honorifics_reporting_and_prompt_persist(self):
  value=copy.deepcopy(PRESETS[1]);value['agents'][0].update(name='최서연',rank='대리',job_title='기획');value['agents'][1].update(rank='사원',job_title='검증',manager_id='builder');save_team(self.store,value);agents=team_settings(Store(self.temp.name))['agents'];self.assertEqual(org.display_name(agents[0]),'최서연 대리 (기획)');self.assertEqual(org.honorific(agents[0]),'최서연 대리님');self.assertEqual(org.snapshot(agents)['roots'],['builder']);self.assertIn('최서연 대리님',frozen_context(self.store)['global_instructions'])
 def test_invalid_reporting_and_slack_name_length_rejected(self):
  for field,value in [('manager_id','builder'),('manager_id','missing'),('rank',True),('job_title','기획'*11)]:
   team=copy.deepcopy(PRESETS[1]);team['agents'][0][field]=value
   with self.subTest(field=field,value=value),self.assertRaises(ValueError):save_team(self.store,team)
  team=copy.deepcopy(PRESETS[1]);team['agents'][0]['manager_id']='checker';team['agents'][1]['manager_id']='builder'
  with self.assertRaises(ValueError):save_team(self.store,team)
  team=copy.deepcopy(PRESETS[1]);team['agents'][0].update(name='가'*30,rank='팀장',job_title='기획')
  with self.assertRaises(ValueError):save_team(self.store,team)
 def test_legacy_names_and_default_four_roles(self):
  self.assertEqual(org.display_name({'name':'지우'}),'지우');self.assertEqual(len(PRESETS[2]['agents']),4);org.validate(PRESETS[2]['agents']);self.assertEqual(len(org.snapshot(PRESETS[2]['agents'])['roots']),1)
if __name__=='__main__':unittest.main()
