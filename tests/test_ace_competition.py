import tempfile,unittest
from pathlib import Path
from app import Store
from setup_config import PRESETS,save_team,team_settings
from peer_scores import competition,summary,ACE_POLICY
from workspace_resources import frozen_context,context

class AceCompetitionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup);self.store=Store(self.temp.name);save_team(self.store,PRESETS[1]);self.agents=team_settings(self.store)['agents'];self.a,self.b=self.agents[:2]
 def award(self,target,rater,tid):
  with self.store.connect() as c:c.execute('INSERT INTO peer_scores(task_id,rater,target,points,reason,evidence) VALUES(?,?,?,?,?,?)',(tid,rater['id'],target['id'],2,'fixture evidence only','[]'))
 def test_highest_and_shared_ace_follow_affinity_without_awarding_points(self):
  initial=summary(self.store);self.assertEqual(competition(self.store)['leaders'],[]);self.assertEqual(summary(self.store),initial)
  self.award(self.a,self.b,'first');self.assertEqual(competition(self.store)['leaders'],[self.a['id']]);self.award(self.b,self.a,'second');self.assertEqual(set(competition(self.store)['leaders']),{self.a['id'],self.b['id']});self.assertEqual(competition(self.store)['highest'],52)
 def test_real_task_context_contains_rank_and_evidence_policy(self):
  self.award(self.a,self.b,'first');before=summary(self.store);frozen=frozen_context(self.store);self.assertIn(ACE_POLICY,frozen['global_instructions']);self.assertIn('"score": 52',frozen['global_instructions']);self.assertIn('자기 평가',frozen['global_instructions']);self.assertIn('근거가 없으면 0점',frozen['global_instructions']);self.assertEqual(summary(self.store),before)
