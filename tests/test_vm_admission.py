import json
from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import patch
from app import Store,Worker
from setup_config import write_setting
from vm_runtime import DEFAULT
from vm_runtime import BusyVM,VMAdmissionBusy,VM
from contextlib import contextmanager

class VMAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.worker=Worker(self.store)
        write_setting(self.store,'vm',{**DEFAULT,'enabled':True})
    def task(self,tid):
        with self.store.connect() as c:return dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
    def test_input_failure_cleanup_never_replays_task_and_next_local_work_runs(self):
        actions=[{'role':'planner','action':'key','keys':['ctrl','t']},{'role':'planner','action':'text','text':'?'},{'role':'planner','action':'click','x':10,'y':20}]
        for action in actions:
            with self.subTest(action=action):
                tid=self.store.add('failed guest input','demo',environment='vm',vm_actions=[action]);next_id=self.store.add('next local task','demo')
                effects=([None] if action['action']=='click' else [])+[TimeoutError('input reply lost'),None]
                with patch.object(self.worker.vm,'_start'),patch.object(self.worker.vm,'capture'),patch.object(self.worker.vm,'qmp',side_effect=effects) as qmp,patch('vm_runtime.time.sleep'),patch('app.Demo.run') as backend:
                    self.worker.tick();backend.assert_not_called();self.assertEqual(qmp.call_count,len(effects))
                self.assertEqual(self.task(tid)['status'],'failed');self.assertIn('자동 재시도하지 않습니다',self.task(tid)['error']);self.assertIsNone(self.worker.vm.owner_record())
                self.worker.tick();self.assertEqual(self.task(next_id)['status'],'completed')
                self.assertFalse(self.worker.tick());self.assertEqual(self.task(tid)['status'],'failed')
    def test_saved_click_default_is_applied_before_any_input(self):
        tid=self.store.add('legacy click default','demo',environment='vm',vm_actions=[{'role':'planner','action':'click','x':10,'y':20}])
        task=self.task(tid);plan=json.loads(task['execution_plan']);del plan['environment']['actions'][0]['button']
        task['execution_plan']=json.dumps(plan)
        with patch.object(self.worker.vm,'_start'),patch.object(self.worker.vm,'capture'),patch.object(self.worker.vm,'qmp') as qmp,patch('vm_runtime.time.sleep'):
            with self.worker.vm.stage(task,'planner','builder'):pass
        buttons=[args.args[1]['events'][0]['data']['button'] for args in qmp.call_args_list if args.args[1]['events'][0]['type']=='btn']
        self.assertEqual(buttons,['left','left']);self.assertIsNone(self.worker.vm.owner_record())
    def test_invalid_saved_actions_never_start_or_send_input(self):
        tid=self.store.add('bad stored input','demo',environment='vm');task=self.task(tid)
        for action in ({'role':'planner','action':'click','x':-1,'y':1},{'role':'planner','action':'key','keys':['unsupported']},{'role':'unknown','action':'capture'}):
            with self.subTest(action=action):
                plan=json.loads(task['execution_plan']);plan['environment']['actions']=[{'role':'planner','action':'click','x':1,'y':1},action]
                damaged={**task,'execution_plan':json.dumps(plan)}
                with patch.object(self.worker.vm,'_start') as start,patch.object(self.worker.vm,'capture') as capture,patch.object(self.worker.vm,'qmp') as qmp:
                    with self.assertRaises(ValueError):
                        with self.worker.vm.stage(damaged,'planner','builder'):self.fail('invalid stage entered')
                start.assert_not_called();capture.assert_not_called();qmp.assert_not_called()
    def test_separate_instances_cannot_overlap_stage_or_replace_owner(self):
        first=self.task(self.store.add('first owner','demo',environment='vm'))
        second=self.task(self.store.add('second owner','demo',environment='vm'))
        contender=VM(self.store);entered=threading.Event();release=threading.Event();errors=[]
        def hold():
            try:
                with self.worker.vm.stage(first,'planner','first-agent'):
                    entered.set()
                    if not release.wait(5):raise AssertionError('stage release timeout')
            except BaseException as error:errors.append(error);entered.set()
        with patch.object(self.worker.vm,'_start'),patch.object(self.worker.vm,'capture'),patch.object(contender,'_start') as start,patch.object(contender,'capture') as capture,patch.object(contender,'action') as action:
            thread=threading.Thread(target=hold);thread.start()
            try:
                self.assertTrue(entered.wait(5));self.assertEqual(errors,[])
                with self.assertRaises(VMAdmissionBusy):
                    with contender.stage(second,'planner','second-agent'):self.fail('overlapping stage')
                start.assert_not_called();capture.assert_not_called();action.assert_not_called()
                self.assertEqual(contender.owner_record()['task_id'],first['id'])
            finally:release.set();thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            with contender.stage(second,'planner','second-agent'):
                self.assertEqual(contender.owner_record()['task_id'],second['id'])
            start.assert_called_once();self.assertEqual(capture.call_count,2)
            self.assertIsNone(contender.owner_record())
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
