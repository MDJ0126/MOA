from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker

class ReportCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.worker=Worker(self.store);self.tid=self.store.add('report publication fixture','demo')
    def test_report_failure_does_not_publish_final_handoff(self):
        original=self.worker.package
        def fail_completed(tid,status,*args,**kwargs):
            if status=='completed':raise OSError('fixture report write failure')
            return original(tid,status,*args,**kwargs)
        with patch.object(self.worker,'package',side_effect=fail_completed):self.worker.tick()
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],'failed')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE task_id=? AND sender='reporter'",(self.tid,)).fetchone()[0],0)

    def test_completion_update_failure_rolls_back_final_notice(self):
        import json
        with self.store.connect() as c:c.execute("CREATE TRIGGER reject_completion BEFORE UPDATE OF status ON tasks WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'fixture completion failure'); END")
        self.worker.tick()
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],'failed')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='reporter'").fetchone()[0],0)
        report=json.loads((self.store.root/'tasks'/self.tid/'report.json').read_text(encoding='utf-8'));self.assertEqual(report['status'],'failed')
    def test_report_includes_committed_final_history_and_receipt(self):
        import json,zipfile
        self.worker.tick();folder=self.store.root/'tasks'/self.tid
        report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(row['agent']=='reporter' and row['kind']=='handoff' for row in report['history']),1)
        with self.store.connect() as c:receipt=json.loads(c.execute('SELECT artifact_publication FROM tasks WHERE id=?',(self.tid,)).fetchone()[0])
        self.assertEqual(report['report_id'],receipt['report_id'])
        with zipfile.ZipFile(folder/'artifacts.zip') as archive:self.assertEqual(json.loads(archive.read('report.json'))['report_id'],report['report_id'])
    def test_partial_report_replacement_is_not_downloadable(self):
        import reports
        from artifacts import open_artifact,ArtifactMissing
        original=reports._replace;failed=False;plan={'agents':[{'id':role} for role in ('planner','developer','reviewer','reporter')]}
        def fail_zip(source,destination):
            nonlocal failed
            if destination.name=='artifacts.zip' and not failed:
                failed=True
                with self.assertRaises(ArtifactMissing):
                    with open_artifact(self.store.root,self.tid,'report.json',plan):pass
                raise OSError('fixture partial report publication')
            return original(source,destination)
        with patch('reports._replace',side_effect=fail_zip):self.worker.tick()
        self.assertTrue(failed)
    def test_actual_child_exit_leaves_uncommitted_reports_hidden_until_recovery(self):
        import subprocess,sys
        from artifacts import open_artifact,ArtifactMissing,metadata
        script="""import os,sys
sys.path.insert(0,sys.argv[1])
from app import Store,Worker
worker=Worker(Store(sys.argv[2]));original=worker.package
def exit_after_publication(tid,status,*args,**kwargs):
 result=original(tid,status,*args,**kwargs)
 if status=='completed':os._exit(75)
 return result
worker.package=exit_after_publication
worker.tick()
raise SystemExit(76)
"""
        result=subprocess.run([sys.executable,'-I','-c',script,str(Path(__file__).resolve().parent.parent/'src'),self.temp.name],capture_output=True,timeout=20)
        self.assertEqual(result.returncode,75,result.stderr.decode(errors='replace'))
        plan={'agents':[{'id':role} for role in ('planner','developer','reviewer','reporter')]}
        self.assertTrue((self.store.root/'tasks'/self.tid/'report.json').is_file())
        with self.assertRaises(ArtifactMissing):
            with open_artifact(self.store.root,self.tid,'report.json',plan):pass
        self.assertEqual(metadata(self.store.root,self.tid,plan)['files'],[])
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],'running')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='reporter'").fetchone()[0],0)
        self.worker.recover();self.worker.tick()
        with open_artifact(self.store.root,self.tid,'report.json',plan) as (stream,info):self.assertGreater(info.st_size,0)
        with self.store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='reporter'").fetchone()[0],1)

    def test_exhausted_review_failure_and_feedback_commit_together(self):
        import json
        from app import Demo
        with self.store.connect() as c:c.execute('UPDATE tasks SET max_revisions=0,revision_budget=0 WHERE id=?',(self.tid,))
        run=Demo.run;deliver=self.store.deliver
        def fail_review(backend,task,role,history,files,log):
            return {'passed':False,'summary':'fixture exhausted review'} if role=='reviewer' else run(backend,task,role,history,files,log)
        def crash_after_feedback(tid,sender,recipient,content,**kwargs):
            result=deliver(tid,sender,recipient,content,**kwargs)
            if sender=='reviewer':raise SystemExit('fixture exhausted review crash')
            return result
        with patch.object(Demo,'run',fail_review),patch.object(self.store,'deliver',side_effect=crash_after_feedback),self.assertRaises(SystemExit):self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='reviewer'").fetchone()[0],0)
        self.worker.recover()
        with patch.object(Demo,'run',fail_review):self.worker.tick()
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],'failed')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='reviewer'").fetchone()[0],1)
        report=json.loads((self.store.root/'tasks'/self.tid/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'failed');self.assertEqual(sum(row['agent']=='reviewer' and row['kind']=='handoff' for row in report['history']),1)

    def test_manual_retry_hides_the_previous_report_publication(self):
        from artifacts import open_artifact,ArtifactMissing
        original=self.worker.package
        def fail_completed(tid,status,*args,**kwargs):
            if status=='completed':raise OSError('fixture completed report failure')
            return original(tid,status,*args,**kwargs)
        with patch.object(self.worker,'package',side_effect=fail_completed):self.worker.tick()
        plan={'agents':[{'id':role} for role in ('planner','developer','reviewer','reporter')]}
        with open_artifact(self.store.root,self.tid,'report.json',plan) as (stream,info):self.assertGreater(info.st_size,0)
        self.store.control('retry',self.tid)
        self.assertTrue((self.store.root/'tasks'/self.tid/'attempts/r0/report.json').is_file())
        with self.assertRaises(ArtifactMissing):
            with open_artifact(self.store.root,self.tid,'report.json',plan):pass
        self.worker.tick()
        with open_artifact(self.store.root,self.tid,'report.json',plan) as (stream,info):self.assertGreater(info.st_size,0)

if __name__=='__main__':unittest.main()
