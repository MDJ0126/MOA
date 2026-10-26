import json,unittest
from test_project_git import ProjectGitTests
from setup_config import write_setting
import project_git as pg
import git_publication as pub

class AutoPublicationTests(ProjectGitTests):
 def test_verified_job_automatically_pushes_readable_folder_and_readme(self):
  remote=self.repository();tid=self.task('계산기 사용 가이드')
  with self.store.connect() as c:
   task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone());plan=json.loads(task['execution_plan']);plan['leader_workflow']={'git':{'publish':True,'reason':'검증한 사용 가이드를 보관합니다.','slug':'calculator-guide'},'refinement':{'goal':'계산기 사용 가이드'}}
   c.execute("UPDATE tasks SET mode='llama',verification=?,execution_plan=? WHERE id=?",(json.dumps({'passed':True}),json.dumps(plan),tid));self.assertTrue(pub.enqueue(self.store,tid,c))
  self.assertTrue(pub.process_one(self.store));receipt=pub.recent(self.store)[tid]['receipt']
  self.assertEqual(receipt['branch'],'work/calculator-guide');self.assertEqual(receipt['path'],'projects/calculator-guide')
  self.assertEqual(pg.git(['--git-dir='+str(remote),'show',receipt['branch']+':'+receipt['path']+'/answer.txt']),'계산기 사용 가이드')
  self.assertIn('계산기 사용 가이드',pg.git(['--git-dir='+str(remote),'show',receipt['branch']+':'+receipt['path']+'/README.md']))
  self.assertEqual(pg.git(['--git-dir='+str(remote),'show',receipt['branch']+':README.md']),'original project');self.assertFalse(pub.process_one(self.store))
 def test_no_upload_instruction_and_nonproject_work_are_skipped(self):
  self.assertFalse(pub.decision({'publish':True,'slug':'test'},{'title':'Git에 올리지 마세요'},'code')['publish'])
  self.assertFalse(pub.decision(None,{'title':'사진 한 장'},'image')['publish'])
  self.assertEqual(pub.decision({'publish':True,'slug':'../escape'},{},'code')['slug'],'')
 def test_unverified_or_disabled_work_never_pushes(self):
  self.repository();tid=self.task('unverified')
  with self.store.connect() as c:
   c.execute("UPDATE tasks SET mode='llama' WHERE id=?",(tid,));self.assertFalse(pub.enqueue(self.store,tid,c))
  self.assertEqual(pub.recent(self.store)[tid]['state'],'skipped')
