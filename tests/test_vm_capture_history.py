import contextlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store
from vm_runtime import VM
class CaptureHistoryTests(unittest.TestCase):
 def test_same_revision_recheck_keeps_all_original_frames(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
   store=Store(folder);tid=store.add('capture history','demo')
   with store.connect() as c:task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
   plan=json.loads(task['execution_plan']);plan['environment']={'kind':'vm','config':{},'actions':[]};task['execution_plan']=json.dumps(plan)
   captured=[];stamps=[]
   def capture(vm,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(str(len(captured)).encode());captured.append(path);return path
   with patch.object(VM,'admission',return_value=contextlib.nullcontext()),patch.object(VM,'_start'),patch.object(VM,'capture',capture),patch('vm_runtime.same_configuration',return_value=True):
    vm=VM(store)
    for i in range(2):
     with vm.stage(task,'reviewer','checker'):stamps.append(task['vm_capture_stamp'])
   self.assertEqual(len(captured),4);self.assertEqual(len(set(captured)),4)
   self.assertEqual(captured[0].read_bytes(),b'0');self.assertEqual(captured[1].read_bytes(),b'1')
   self.assertNotEqual(stamps[0],stamps[1]);self.assertIn('visit2',stamps[1])
