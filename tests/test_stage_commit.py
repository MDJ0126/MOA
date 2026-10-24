from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo

class StageCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.worker=Worker(self.store);self.tid=self.store.add('stage commit fixture','demo')
    def count(self,actor):
        with self.store.connect() as c:return c.execute("SELECT COUNT(*) FROM messages WHERE task_id=? AND agent=? AND kind='handoff'",(self.tid,actor)).fetchone()[0]
    def test_crash_after_handoff_does_not_duplicate_stage_on_recovery(self):
        original=self.store.deliver
        def crash(tid,sender,recipient,content,**kwargs):
            result=original(tid,sender,recipient,content,**kwargs)
            if sender=='planner':raise SystemExit('fixture process crash after handoff')
            return result
        with patch.object(self.store,'deliver',side_effect=crash),self.assertRaises(SystemExit):self.worker.tick()
        self.assertEqual(self.count('planner'),0,'uncommitted stage must not publish a handoff')
        self.worker.recover();self.worker.tick();self.assertEqual(self.count('planner'),1)
    def test_crash_after_review_feedback_does_not_duplicate_revision_feedback(self):
        original=self.store.deliver;run=Demo.run
        def failing_review(backend,task,role,history,files,log):
            return {'passed':False,'summary':'fixture changes needed'} if role=='reviewer' else run(backend,task,role,history,files,log)
        def crash(tid,sender,recipient,content,**kwargs):
            result=original(tid,sender,recipient,content,**kwargs)
            if sender=='reviewer':raise SystemExit('fixture process crash after feedback')
            return result
        with patch.object(Demo,'run',failing_review),patch.object(self.store,'deliver',side_effect=crash),self.assertRaises(SystemExit):self.worker.tick()
        self.assertEqual(self.count('reviewer'),0)
        self.worker.recover()
        with patch.object(Demo,'run',failing_review):self.worker.tick()
        with self.store.connect() as c:task=c.execute('SELECT status,stage,revision FROM tasks WHERE id=?',(self.tid,)).fetchone()
        self.assertEqual(tuple(task),('queued',1,1));self.assertEqual(self.count('reviewer'),1)

    def test_stage_update_failure_rolls_back_delivery_and_can_be_retried(self):
        import sqlite3
        with self.store.connect() as c:c.execute("CREATE TRIGGER reject_stage_move BEFORE UPDATE OF stage ON tasks WHEN NEW.stage=1 AND OLD.stage=0 BEGIN SELECT RAISE(ABORT,'fixture stage update failure'); END")
        self.worker.tick()
        with self.store.connect() as c:
            self.assertEqual(tuple(c.execute('SELECT status,stage FROM tasks WHERE id=?',(self.tid,)).fetchone()),('failed',0))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='planner'").fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
            c.execute('DROP TRIGGER reject_stage_move')
        self.assertEqual(self.count('planner'),0)
        self.store.control('retry',self.tid);self.worker.tick();self.assertEqual(self.count('planner'),1)
    def test_revision_update_failure_rolls_back_feedback(self):
        run=Demo.run
        def failing_review(backend,task,role,history,files,log):
            return {'passed':False,'summary':'fixture changes needed'} if role=='reviewer' else run(backend,task,role,history,files,log)
        with self.store.connect() as c:c.execute("CREATE TRIGGER reject_revision_move BEFORE UPDATE OF revision ON tasks WHEN NEW.revision>OLD.revision BEGIN SELECT RAISE(ABORT,'fixture revision update failure'); END")
        with patch.object(Demo,'run',failing_review):self.worker.tick()
        with self.store.connect() as c:
            self.assertEqual(tuple(c.execute('SELECT status,stage,revision FROM tasks WHERE id=?',(self.tid,)).fetchone()),('failed',2,0))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='reviewer'").fetchone()[0],0)
        self.assertEqual(self.count('reviewer'),0)

    def test_actual_child_exit_rolls_back_handoff_before_recovery(self):
        import subprocess,sys
        script="""import os,sys
sys.path.insert(0,sys.argv[1])
from app import Store,Worker
store=Store(sys.argv[2]);original=store.deliver
def exit_after_handoff(tid,sender,recipient,content,**kwargs):
 result=original(tid,sender,recipient,content,**kwargs)
 if sender=='planner':os._exit(73)
 return result
store.deliver=exit_after_handoff
Worker(store).tick()
raise SystemExit(74)
"""
        result=subprocess.run([sys.executable,'-I','-c',script,str(Path(__file__).resolve().parent.parent/'src'),self.temp.name],capture_output=True,timeout=20)
        self.assertEqual(result.returncode,73,result.stderr.decode(errors='replace'))
        with self.store.connect() as c:self.assertEqual(tuple(c.execute('SELECT status,stage FROM tasks WHERE id=?',(self.tid,)).fetchone()),('running',0))
        self.assertEqual(self.count('planner'),0)
        self.worker.recover();self.worker.tick();self.assertEqual(self.count('planner'),1)

if __name__=='__main__':unittest.main()

