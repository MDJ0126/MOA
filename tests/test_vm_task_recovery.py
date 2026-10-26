import tempfile,unittest,json
from contextlib import nullcontext
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from app import Store,Worker
from vm_runtime import VMRecoveryPending
class TaskRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.store=Store(self.temp.name);self.worker=Worker(self.store)
 def tearDown(self):self.temp.cleanup()
 def test_disconnection_requeues_same_stage_without_failure_or_delivery(self):
  tid=self.store.add('recovery boundary','demo')
  @contextmanager
  def broken(*args,**kwargs):raise VMRecoveryPending('reboot');yield
  with patch.object(self.worker.vm,'stage',broken):self.worker.tick()
  with self.store.connect() as c:r=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
  self.assertEqual((r['status'],r['stage'],r['failures'],r['wait_reason']),('queued',0,0,'vm_recovery'))
  self.assertGreater(r['retry_at'],0)
  with self.store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='handoff'",(tid,)).fetchone()[0],0)
 def test_ready_guest_resumes_only_unfinished_stage(self):
  tid=self.store.add('resume developer stage','demo')
  with self.store.connect() as c:c.execute("UPDATE tasks SET stage=1 WHERE id=?",(tid,))
  @contextmanager
  def broken(*args,**kwargs):raise VMRecoveryPending('reboot');yield
  with patch.object(self.worker.vm,'stage',broken):self.worker.tick()
  with self.store.connect() as c:
   self.assertEqual(c.execute('SELECT stage FROM tasks WHERE id=?',(tid,)).fetchone()[0],1)
   c.execute('UPDATE tasks SET retry_at=0 WHERE id=?',(tid,))
  self.worker.tick()
  with self.store.connect() as c:
   self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'completed')
   self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='start' AND capability='planner'",(tid,)).fetchone()[0],0)
 def test_cancelled_request_not_requeued_on_disconnect(self):
  tid=self.store.add('cancel boundary','demo')
  @contextmanager
  def broken(*args,**kwargs):self.store.control('cancel',tid);raise VMRecoveryPending('reboot');yield
  with patch.object(self.worker.vm,'stage',broken):self.worker.tick()
  with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'cancelled')

 def test_paused_whpx_reboot_fault_is_restarted_and_requeued(self):
  from vm_runtime import settings
  tid=self.store.add('WHPX recovery','demo');v=self.worker.vm
  task={'id':tid,'revision':0,'execution_plan':json.dumps({'environment':{'kind':'vm','config':settings(self.store)},'pipeline':['developer']})}
  (v.folder/'qemu.log').write_text('WHPX: Unexpected VP exit code 4')
  with patch.object(v,'admission',side_effect=lambda:nullcontext()),patch.object(v,'session',return_value={'config':{'accelerator':'whpx','guest_os':'windows'}}),patch.object(v,'qmp',return_value={'status':'paused'}),patch.object(v,'capture'),patch.object(v,'_stop') as stop,patch.object(v,'_start') as start:
   with self.assertRaises(VMRecoveryPending):
    with v.stage(task,'developer','builder'):pass
  stop.assert_called_once();start.assert_called_once()
