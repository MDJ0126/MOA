import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker
from setup_config import write_setting
from vm_runtime import DEFAULT
from vm_runtime import BusyVM,VMAdmissionBusy
from contextlib import contextmanager

class VMAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.worker=Worker(self.store)
        write_setting(self.store,'vm',{**DEFAULT,'enabled':True})
    def task(self,tid):
        with self.store.connect() as c:return dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
    def test_busy_reviewer_does_not_repeat_checks_or_checkpoint(self):
        tid=self.store.add('busy reviewer','ollama',environment='vm');plan=json.loads(self.task(tid)['execution_plan'])
        with self.store.connect() as c:c.execute('UPDATE tasks SET stage=? WHERE id=?',(plan['pipeline'].index('reviewer'),tid))
        with self.worker.vm.exclusive(),patch('app.run_checks',return_value={'passed':True,'tests_run':1}) as checks,patch.object(self.worker,'checkpoint') as checkpoint:
            self.worker.tick()
        checks.assert_not_called();checkpoint.assert_not_called()
        self.assertEqual(self.task(tid)['status'],'queued')

    def test_repeated_admission_wait_is_logged_once_without_failure_budget(self):
        tid=self.store.add('wait without effects','demo',environment='vm')
        with self.worker.vm.exclusive(),patch.object(self.worker.vm,'_start') as start,patch.object(self.worker.vm,'capture') as capture:
            self.worker.tick()
            with self.store.connect() as c:c.execute('UPDATE tasks SET retry_at=0 WHERE id=?',(tid,))
            self.worker.tick()
        task=self.task(tid);self.assertEqual(task['wait_reason'],'vm_access');self.assertEqual(task['failures'],0);self.assertEqual(task['stage'],0)
        start.assert_not_called();capture.assert_not_called()
        messages=[m for m in self.store.snapshot()['messages'] if m['task_id']==tid and m['kind']=='control']
        self.assertEqual(len(messages),1);self.assertEqual(messages[0]['capability'],'planner')

    def test_cancel_during_admission_does_not_return_to_queue(self):
        tid=self.store.add('cancel while waiting','demo',environment='vm')
        @contextmanager
        def admission():
            self.store.control('cancel',tid);raise VMAdmissionBusy('busy')
            yield
        with patch.object(self.worker.vm,'admission',admission):self.worker.tick()
        self.assertEqual(self.task(tid)['status'],'cancelled')

    def test_busy_error_after_input_is_not_automatically_replayed(self):
        tid=self.store.add('late busy','demo',environment='vm',vm_actions=[{'role':'planner','action':'key','keys':['ret']}])
        with patch.object(self.worker.vm,'_start'),patch.object(self.worker.vm,'capture'),patch.object(self.worker.vm,'action') as action,patch('app.Demo.run',side_effect=BusyVM('late adapter error')):
            self.worker.tick();self.assertEqual(self.task(tid)['status'],'failed');self.assertFalse(self.worker.tick())
            self.assertEqual(action.call_count,1)

    def test_audit_failure_holds_task_and_next_local_task_runs(self):
        tid=self.store.add('audit failure','demo',environment='vm');next_id=self.store.add('next local task','demo')
        with self.store.connect() as c:c.execute("CREATE TRIGGER reject_wait BEFORE INSERT ON messages WHEN NEW.kind='control' BEGIN SELECT RAISE(ABORT,'wait audit failure'); END")
        with self.worker.vm.exclusive():self.worker.tick();self.worker.tick()
        self.assertEqual(self.task(tid)['status'],'interrupted');self.assertEqual(self.task(next_id)['status'],'completed')
        self.assertNotEqual(self.task(tid)['wait_reason'],'vm_access')

    def test_reporter_revalidation_is_before_any_vm_effect(self):
        tid=self.store.add('reporter stale files','demo',environment='vm',vm_actions=[{'role':'reporter','action':'capture'}]);plan=json.loads(self.task(tid)['execution_plan'])
        with self.store.connect() as c:c.execute('UPDATE tasks SET stage=?,verification=? WHERE id=?',(plan['pipeline'].index('reporter'),json.dumps({'source_hashes':{'changed':'0'}}),tid))
        with patch.object(self.worker.vm,'_start') as start,patch.object(self.worker.vm,'capture') as capture:
            self.worker.tick()
        start.assert_not_called();capture.assert_not_called();self.assertEqual(self.task(tid)['status'],'queued');self.assertEqual(self.task(tid)['stage'],plan['pipeline'].index('reviewer'))
        with self.worker.vm.exclusive():pass

if __name__=='__main__':unittest.main()
