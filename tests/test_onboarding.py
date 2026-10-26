import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store
from setup_config import PRESETS,save_team,team_settings
import onboarding as ob
import workspace_resources as wr

class OnboardingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup);self.s=Store(self.temp.name)
  self.inv=patch.object(wr,'inventory',return_value={'programs':[{**p,'installed':False} for p in wr.PROGRAMS]});self.inv.start();self.addCleanup(self.inv.stop)
  self.hw=patch.object(ob,'hardware',return_value={'recommended_preset':'light'});self.hw.start();self.addCleanup(self.hw.stop)
 def test_first_run_skip_manual_and_reopen_status(self):
  self.assertTrue(ob.status(self.s)['needed'])
  before=team_settings(self.s);ob.save(self.s,{'action':'skip'});self.assertFalse(ob.status(self.s)['needed']);self.assertEqual(before,team_settings(self.s))
  self.assertEqual(ob.save(self.s,{'action':'manual'})['state'],'manual');self.assertEqual(len(ob.status(self.s)['tasks']),5)
 def test_existing_users_do_not_get_interrupted(self):
  save_team(self.s,PRESETS[1]);self.assertFalse(ob.status(self.s)['needed'])
 def test_engine_choices_and_recommendations(self):
  rec=ob.recommend(self.s,{'tasks':['game'],'engine':'unity'});ids=[p['id'] for p in rec['programs']];self.assertIn('unity',ids);self.assertNotIn('unreal',ids);self.assertIn('unity-game',[s['id'] for s in rec['skills']])
  rec=ob.recommend(self.s,{'tasks':['game'],'engine':'undecided'});self.assertFalse(any(p['id'] in ('unity','unreal') for p in rec['programs']))
  with self.assertRaises(ValueError):ob.recommend(self.s,{'tasks':[]})
 def test_apply_is_atomic_idempotent_and_does_not_install(self):
  value={'action':'apply','tasks':['web'],'engine':'undecided','programs':['git'],'skills':['quality-check'],'apply_team':True}
  one=ob.save(self.s,value);two=ob.save(self.s,value);self.assertEqual(one,two);self.assertEqual(team_settings(self.s)['preset'],'standard');self.assertTrue(next(s for s in wr.skills(self.s) if s['id']=='quality-check')['installed'])
  with self.s.connect() as c:self.assertFalse(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='resource_jobs'").fetchone())
 def test_existing_team_and_custom_preferences_survive(self):
  team=copy.deepcopy(PRESETS[1]);team['agents'][0]['name']='사용자 이름';team['agents'][0]['instructions']='기존 지침';save_team(self.s,team);before=team_settings(self.s)
  with self.assertRaises(ValueError):ob.save(self.s,{'action':'apply','tasks':['data'],'skills':['python-work'],'programs':[],'apply_team':True})
  self.assertEqual(before,team_settings(self.s));self.assertFalse(ob.status(self.s)['saved'])
  ob.save(self.s,{'action':'apply','tasks':['data'],'skills':['python-work'],'programs':[],'apply_team':False});self.assertEqual(before,team_settings(self.s))
  with self.assertRaises(ValueError):ob.save(self.s,{'action':'apply','tasks':['research'],'programs':['unreal'],'skills':[]})

if __name__=='__main__':unittest.main()
