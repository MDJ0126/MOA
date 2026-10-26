import tempfile,unittest,base64,json
from pathlib import Path
from unittest.mock import patch
from app import Store
from setup_config import save_team,PRESETS
import team_profiles as p
from test_office_chat import png
class CandidateTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.t.cleanup);self.store=Store(self.t.name);save_team(self.store,PRESETS[1]);self.g=Path(self.t.name)/'gallery';self.g.mkdir();self.patch=patch.object(p,'GALLERY',self.g);self.patch.start();self.addCleanup(self.patch.stop)
  (self.g/'manifest.json').write_text(json.dumps([{'id':f'{g}-{i:02}','gender':g,'label':g+str(i)} for g in ('male','female') for i in range(1,11)]))
  for g in ('male','female'):
   for i in range(1,11):(self.g/f'{g}-{i:02}.png').write_bytes(png())
 def test_new_teammate_gets_gender_matched_photo_and_existing_photos_stay(self):
  import copy
  before=p.public(self.store)
  team=copy.deepcopy(PRESETS[1]);team['agents'].append({'id':'new-member','name':'신규 팀원','capabilities':['developer'],'gender':'female'})
  save_team(self.store,team)
  after=p.public(self.store);self.assertEqual(after['new-member']['gender'],'female');self.assertTrue(after['new-member']['candidate_id'].startswith('female-'))
  self.assertIsNotNone(p.image_path(self.store,'new-member'))
  for key,value in before.items():self.assertEqual(value,after[key])
  self.assertEqual(p.ensure_defaults(self.store),[])
  save_team(self.store,team);self.assertEqual(p.public(self.store),after)
 def test_missing_existing_photos_are_backfilled(self):
  from setup_config import write_setting
  write_setting(self.store,'agent_profiles',{})
  self.assertEqual(set(p.ensure_defaults(self.store)),{'builder','checker'})
  for value in p.public(self.store).values():self.assertNotEqual(value['image_hash'],'default')
 def test_random_is_gender_filtered_persisted_and_reload_stable(self):
  saved=p.save(self.store,{'agent_id':'builder','gender':'male','random':True})['builder'];self.assertEqual(saved['gender'],'male');self.assertTrue(saved['candidate_id'].startswith('male-'));self.assertEqual(saved,p.public(Store(self.t.name))['builder']);self.assertEqual(len(p.candidates(self.store,'builder')['candidates']),10)
 def test_candidate_selection_and_upload_preserve_gender(self):
  p.save(self.store,{'agent_id':'checker','candidate_id':'female-07','gender':'female'});self.assertEqual(p.public(self.store)['checker']['candidate_id'],'female-07');p.save(self.store,{'agent_id':'checker','image_base64':base64.b64encode(png()).decode()});self.assertEqual(p.public(self.store)['checker']['gender'],'female');self.assertEqual(p.public(self.store)['checker']['candidate_id'],'')
 def test_reject_cross_gender_path_unknown_agent_and_random_without_gender(self):
  for value in ({'agent_id':'builder','gender':'male','candidate_id':'female-01'},{'agent_id':'builder','candidate_id':'../../secret'},{'agent_id':'missing','gender':'male','random':True},{'agent_id':'builder','random':True}):
   with self.assertRaises(ValueError):p.save(self.store,value)
if __name__=='__main__':unittest.main()
