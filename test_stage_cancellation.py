from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo
from setup_config import write_setting,read_setting
from vm_runtime import DEFAULT


class StageCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.worker=Worker(self.store)
        write_setting(self.store,'vm',{**DEFAULT,'enabled':True})
    def task(self,tid):
        with self.store.connect() as connection:return dict(connection.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())

    def test_cancel_on_last_vm_action_does_not_enter_stage_body(self):
        tid=self.store.add('cancel on last input','demo',environment='vm',vm_actions=[{'role':'planner','action':'key','keys':['ret']}]);task=self.task(tid)
        with patch.object(self.worker.vm,'_start'),patch.object(self.worker.vm,'capture'),patch.object(self.worker.vm,'action',side_effect=lambda action:self.store.control('cancel',tid)):
            with self.assertRaisesRegex(ValueError,'취소'):
                with self.worker.vm.stage(task,'planner','planner'):self.fail('cancelled stage entered')
        self.assertIsNone(read_setting(self.store,'vm_owner',None))
        with self.worker.vm.exclusive():pass

    def test_cancel_during_before_capture_skips_model_and_final_capture(self):
        tid=self.store.add('cancel during capture','demo',environment='vm')
        with patch.object(self.worker.vm,'_start'),patch.object(self.worker.vm,'capture',side_effect=lambda path:self.store.control('cancel',tid)) as capture,patch.object(Demo,'run') as run:
            self.worker.tick()
        run.assert_not_called();self.assertEqual(capture.call_count,1)
        self.assertEqual(self.task(tid)['status'],'cancelled');self.assertEqual(self.task(tid)['failures'],0)
        self.assertIsNone(read_setting(self.store,'vm_owner',None))

    def test_cancel_on_last_input_skips_model_and_next_local_task_runs(self):
        tid=self.store.add('cancel at final input','demo',environment='vm',vm_actions=[{'role':'planner','action':'key','keys':['ret']}]);following=self.store.add('following local task','demo')
        with patch.object(self.worker.vm,'_start'),patch.object(self.worker.vm,'capture'),patch.object(self.worker.vm,'action',side_effect=lambda action:self.store.control('cancel',tid)) as action,patch.object(Demo,'run') as run:
            self.worker.tick()
        run.assert_not_called();action.assert_called_once()
        self.assertEqual(self.task(tid)['status'],'cancelled');self.assertEqual(self.task(tid)['failures'],0)
        self.worker.tick();self.assertEqual(self.task(following)['status'],'completed')
        with self.worker.vm.exclusive():pass

    def test_cancel_during_local_preparation_skips_model(self):
        tid=self.store.add('cancel during local checkpoint','demo')
        with patch.object(self.worker,'checkpoint',side_effect=lambda *args:self.store.control('cancel',tid)),patch.object(Demo,'run') as run:
            self.worker.tick()
        run.assert_not_called();self.assertEqual(self.task(tid)['status'],'cancelled');self.assertEqual(self.task(tid)['failures'],0)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM messages WHERE task_id=? AND kind='handoff'",(tid,)).fetchone()[0],0)
