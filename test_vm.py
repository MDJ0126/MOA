import copy
import json
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

class VMTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.store=Store(self.temp.name);self.vm=VM(self.store)
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
