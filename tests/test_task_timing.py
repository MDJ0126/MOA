import tempfile,unittest,time
from pathlib import Path
from app import Store
import task_timing

class TimingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.s=Store(self.root/'data')
  with self.s.connect() as c:c.execute("INSERT INTO tasks(id,title,status,stage,created) VALUES('t','test','queued',0,1000)")
 def state(self,status,stage,at):
  with self.s.connect() as c:
   c.execute('UPDATE tasks SET status=?,stage=? WHERE id=?',(status,stage,'t'))
   c.execute('UPDATE task_timing_events SET recorded=? WHERE seq=(SELECT MAX(seq) FROM task_timing_events)',(at,))
 def timing(self,now):
  with self.s.connect() as c:return task_timing.enrich(c,[dict(c.execute("SELECT * FROM tasks WHERE id='t'").fetchone())],now)[0]['timing']
 def test_queue_stage_retry_and_frozen_end(self):
  self.state('running',0,1010);self.state('running',1,1020);self.state('queued',1,1030);self.state('running',1,1040);self.state('completed',2,1060)
  t=self.timing(2000);self.assertEqual(t['elapsed_seconds'],60);self.assertEqual(t['active_seconds'],40);self.assertEqual(t['stages'],[{'stage':0,'seconds':10},{'stage':1,'seconds':30}]);self.assertEqual(t['finished_at'],1060)
  Store(self.root/'data');self.assertEqual(self.timing(3000)['elapsed_seconds'],60)
 def test_live_duration_cancel_and_retry(self):
  self.state('running',0,1010);self.assertEqual(self.timing(1025)['active_seconds'],15)
  self.state('cancelled',0,1030);self.assertEqual(self.timing(9000)['elapsed_seconds'],30)
  self.state('queued',0,1040);self.state('running',0,1050);self.state('completed',1,1070)
  self.assertEqual(self.timing(2000)['active_seconds'],40)
 def test_trigger_rollback_is_atomic(self):
  with self.assertRaises(RuntimeError):
   with self.s.connect() as c:c.execute("UPDATE tasks SET status='running' WHERE id='t'");raise RuntimeError()
  with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM task_timing_events').fetchone()[0],1)
 def test_old_logs_are_marked_estimated(self):
  with self.s.connect() as c:
   c.execute('DELETE FROM task_timing_events');c.execute("UPDATE tasks SET status='completed' WHERE id='t'");c.execute('DELETE FROM task_timing_events')
   c.execute("INSERT INTO messages(task_id,kind,created) VALUES('t','start',1010)");c.execute("INSERT INTO messages(task_id,kind,created) VALUES('t','handoff',1030)")
  t=self.timing(5000);self.assertTrue(t['estimated']);self.assertEqual(t['elapsed_seconds'],20);self.assertIsNone(t['active_seconds'])
 def test_real_demo_worker_records_each_stage(self):
  from app import Worker
  with self.s.connect() as c:c.execute("DELETE FROM tasks WHERE id='t'")
  tid=self.s.add('timing demo verification','demo');worker=Worker(self.s,demo_only=True)
  for _ in range(8):
   worker.tick()
   task=next(t for t in self.s.snapshot()['tasks'] if t['id']==tid)
   if task['status']=='completed':break
  self.assertEqual(task['status'],'completed');self.assertFalse(task['timing']['estimated']);self.assertTrue(task['timing']['stages']);self.assertGreaterEqual(task['timing']['elapsed_seconds'],task['timing']['active_seconds'])
