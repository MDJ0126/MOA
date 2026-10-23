import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import Store,Worker,Demo
from setup_config import PRESETS,save_team,write_setting
from vm_runtime import VM,BusyVM,DEFAULT,validate_actions
from supervisor import write_status
from setup_config import SettingsConflict,read_setting

class VMTests(unittest.TestCase):
    def test_stale_settings_are_rejected_without_overwrite(self):
        self.vm.save(dict(DEFAULT,enabled=True,expected_updated=0))
        from vm_runtime import settings
        current=settings(self.store)
        with self.assertRaises(SettingsConflict):self.vm.save(dict(DEFAULT,memory_mb=1024,expected_updated=0))
        self.assertEqual(settings(self.store),current)
        saved=self.vm.save(dict(DEFAULT,enabled=True,memory_mb=1024,expected_updated=current['updated']))
        self.assertEqual(saved['memory_mb'],1024);self.assertGreater(saved['updated'],current['updated'])

    def test_same_values_new_revision_does_not_invalidate_frozen_work(self):
        self.store.add('same environment','demo',environment='vm');task=self.store.snapshot()['tasks'][0]
        with patch.object(self.vm,'session',return_value={}):self.vm.save(dict(DEFAULT,enabled=True,expected_updated=0))
        with patch.object(self.vm,'_start') as start,patch.object(self.vm,'capture'):
            with self.vm.stage(task,'developer','builder'):pass
            start.assert_called_once()

    def test_cancelled_before_acquisition_does_not_start_or_capture(self):
        tid=self.store.add('cancelled','demo',environment='vm');task=self.store.snapshot()['tasks'][0];self.store.control('cancel',tid)
        with patch.object(self.vm,'_start') as start,patch.object(self.vm,'capture') as capture,patch.object(self.vm,'action') as action:
            with self.assertRaisesRegex(ValueError,'취소'):
                with self.vm.stage(task,'developer','builder'):self.fail('cancelled stage entered')
            start.assert_not_called();capture.assert_not_called();action.assert_not_called()

    def test_cancel_during_stage_skips_final_capture_and_releases_owner(self):
        tid=self.store.add('cancel during stage','demo',environment='vm');task=self.store.snapshot()['tasks'][0]
        with patch.object(self.vm,'_start'),patch.object(self.vm,'capture') as capture:
            with self.vm.stage(task,'developer','builder'):self.store.control('cancel',tid)
            self.assertEqual(capture.call_count,1)
        self.assertIsNone(read_setting(self.store,'vm_owner',None))

    def test_cancel_between_actions_stops_remaining_actions(self):
        actions=[{'role':'developer','action':'key','keys':['a']},{'role':'developer','action':'key','keys':['b']}]
        tid=self.store.add('cancel between actions','demo',environment='vm',vm_actions=actions);task=self.store.snapshot()['tasks'][0]
        def cancel_after_first(action):self.store.control('cancel',tid)
        with patch.object(self.vm,'_start'),patch.object(self.vm,'capture') as capture,patch.object(self.vm,'action',side_effect=cancel_after_first) as action:
            with self.assertRaisesRegex(ValueError,'취소'):
                with self.vm.stage(task,'developer','builder'):self.fail('cancelled stage entered')
            self.assertEqual(action.call_count,1);self.assertEqual(capture.call_count,1)
        self.assertIsNone(read_setting(self.store,'vm_owner',None))

    @unittest.skipUnless(os.name=='nt','Windows byte-range lock')
    def test_unlock_error_still_closes_handle_and_releases_thread_lock(self):
        import msvcrt
        locking=msvcrt.locking
        def fail_unlock(fd,mode,size):
            if mode==msvcrt.LK_UNLCK:raise OSError('unlock failed')
            return locking(fd,mode,size)
        with patch('msvcrt.locking',side_effect=fail_unlock):
            with self.assertRaisesRegex(OSError,'unlock failed'):
                with self.vm.exclusive():pass
        self.assertFalse(self.vm.thread_lock.locked())
        with VM(self.store).exclusive():pass
        with self.vm.exclusive():pass

    def test_acquire_log_failure_still_releases_owner_and_lock(self):
        self.store.add('log error','demo',environment='vm');task=self.store.snapshot()['tasks'][0];log=self.store.log
        def fail_acquire(tid,actor,kind,content,**kwargs):
            if kind=='vm_acquire':raise OSError('log unavailable')
            return log(tid,actor,kind,content,**kwargs)
        with patch.object(self.vm,'_start'),patch.object(self.vm,'capture'),patch.object(self.store,'log',side_effect=fail_acquire):
            with self.assertRaisesRegex(OSError,'log unavailable'):
                with self.vm.stage(task,'developer','builder'):pass
        self.assertIsNone(read_setting(self.store,'vm_owner',None))
        with self.vm.exclusive():pass

    def test_unreachable_live_vm_cannot_change_settings(self):
        old=copy.deepcopy(DEFAULT)
        with patch.object(self.vm,'session',return_value={'pid':123}), patch.object(self.vm,'qmp',side_effect=OSError('offline')), patch('vm_runtime.pid_alive',return_value=True):
            with self.assertRaisesRegex(ValueError,'실행 중'):self.vm.save(dict(old,enabled=False))
        from vm_runtime import settings
        self.assertTrue(settings(self.store)['enabled'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.store=Store(self.temp.name);self.vm=VM(self.store)
        self.project_patch=patch('vm_runtime.ROOT',Path(self.temp.name));self.project_patch.start();self.addCleanup(self.project_patch.stop)
        iso=Path(self.temp.name)/DEFAULT['iso'];iso.parent.mkdir(parents=True,exist_ok=True);iso.write_bytes(b'unit fixture only; not a bootable ISO')
        save_team(self.store,PRESETS[1]);write_setting(self.store,'vm',dict(DEFAULT,enabled=True))
    def tearDown(self):self.temp.cleanup()
    def test_actions_validate_types_and_limits(self):
        for bad in ([{'role':'developer','action':'shell'}],[{'role':'developer','action':'click','x':True,'y':1}],
                    [{'role':'developer','action':'key','keys':['not-a-key']}],[{'role':'missing','action':'capture'}],
                    [{'role':'developer','action':'wait','seconds':10}]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate_actions(bad,['developer'])
        self.assertEqual(validate_actions([{'role':'developer','action':'key','keys':['ctrl','alt','t']}],['developer'])[0]['keys'],['ctrl','alt','t'])
    def test_environment_is_explicit_and_frozen(self):
        tid=self.store.add('vm','demo',environment='vm',vm_actions=[{'role':'reviewer','action':'capture'}])
        plan=json.loads(self.store.snapshot()['tasks'][0]['execution_plan'])
        self.assertEqual(plan['environment']['config']['memory_mb'],512)
        write_setting(self.store,'vm',dict(DEFAULT,enabled=True,memory_mb=1024))
        with self.assertRaisesRegex(ValueError,'변경'):
            with self.vm.stage(self.store.snapshot()['tasks'][0],'developer','builder'):pass
        with self.assertRaises(ValueError):self.store.add('invalid','demo',vm_actions=[{'role':'reviewer','action':'capture'}])
    def test_exclusive_lock_rejects_same_and_other_process(self):
        with self.vm.exclusive():
            with self.assertRaises(BusyVM):
                with self.vm.exclusive():pass
            script='from app import Store;from vm_runtime import VM,BusyVM;import sys\ntry:\n with VM(Store(sys.argv[1])).exclusive():sys.exit(2)\nexcept BusyVM:sys.exit(0)'
            result=subprocess.run([sys.executable,'-c',script,self.temp.name],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
        with self.vm.exclusive():pass
    def test_stage_failure_releases_owner_and_captures_are_recorded(self):
        tid=self.store.add('vm','demo',environment='vm');task=self.store.snapshot()['tasks'][0]
        with patch.object(self.vm,'_start'),patch.object(self.vm,'capture'):
            with self.assertRaises(RuntimeError):
                with self.vm.stage(task,'developer','builder'):
                    self.assertEqual(self.vm.status()['owner']['agent'],'builder');raise RuntimeError('test failure')
        self.assertIsNone(self.vm.status()['owner'])
        kinds=[m['kind'] for m in self.store.snapshot()['messages']]
        self.assertIn('vm_acquire',kinds);self.assertIn('vm_release',kinds)
        with self.vm.exclusive():pass
    def test_busy_worker_waits_without_failing_and_local_work_is_unchanged(self):
        tid=self.store.add('waiting','demo',environment='vm');worker=Worker(self.store)
        with worker.vm.exclusive():worker.tick()
        task=self.store.snapshot()['tasks'][0];self.assertEqual(task['status'],'queued');self.assertEqual(task['failures'],0)
        self.store.control('cancel',tid)
        local=self.store.add('local','demo');worker.tick()
        self.assertEqual(next(t for t in self.store.snapshot()['tasks'] if t['id']==local)['status'],'completed')
    def test_configuration_rejects_escape_and_unsupported_resources(self):
        for change in ({'iso':'../outside.iso'},{'memory_mb':8192},{'cpus':8},{'accelerator':'arbitrary'},{'network':'false'}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.vm.save(dict(DEFAULT,**change))

    def test_unreachable_live_vm_does_not_spawn_a_second_vm(self):
        self.vm.session_file.write_text(json.dumps({'pid':123,'port':1,'name':'owned'}))
        with patch.object(self.vm,'qmp',side_effect=ConnectionError('offline')),patch('vm_runtime.pid_alive',return_value=True),patch('vm_runtime.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'중복'):self.vm.start()
            spawn.assert_not_called()

    def test_text_is_bounded_and_guest_only(self):
        for value in ('한글','x'*161,'\x00'):
            with self.assertRaises(ValueError):validate_actions([{'role':'developer','action':'text','text':value}],['developer'])
        calls=[]
        with patch.object(self.vm,'qmp',side_effect=lambda command,args:calls.append((command,args))),patch('vm_runtime.time.sleep'):
            self.vm.action({'action':'text','text':'aB!\n'})
        self.assertTrue(all(name=='input-send-event' for name,args in calls))
        self.assertTrue(any(event['data']['key']['data']=='shift' for name,args in calls for event in args['events']))

    def test_status_reader_lock_is_nonfatal_and_recovers_on_next_write(self):
        target=Path(self.temp.name)/'supervisor.json'
        with patch.object(Path,'replace',side_effect=PermissionError('reader')),patch('supervisor.time.sleep'):
            self.assertFalse(write_status(target,{'pid':123}))
        self.assertTrue(write_status(target,{'pid':456}));self.assertEqual(json.loads(target.read_text())['pid'],456)

if __name__=='__main__':unittest.main()
