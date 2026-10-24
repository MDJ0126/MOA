import os
import subprocess
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from app import Store
from setup_config import write_setting
from vm_runtime import VM,DEFAULT


class VMRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.root=Path(self.temp.name);self.store=Store(self.root/'data')
        self.root_patch=patch('vm_runtime.ROOT',self.root);self.root_patch.start();self.addCleanup(self.root_patch.stop)
        self.hardware_patch=patch('vm_runtime.hardware',return_value={'available_ram_gib':8});self.hardware_patch.start();self.addCleanup(self.hardware_patch.stop)
        self.vm=VM(self.store);write_setting(self.store,'vm',dict(DEFAULT,enabled=True))
        for relative in (DEFAULT['iso'],'runtime/qemu/qemu-system-x86_64.exe'):
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture only, never executed')
        self.child=Mock(pid=12345);self.child.poll.return_value=None;self.child.wait.return_value=0
    def tearDown(self):self.temp.cleanup()

    def test_invalid_session_never_reuses_or_spawns_guest(self):
        for raw in (b'{broken',b'[]',b'{}',b'null',b'\xff',json.dumps({'pid':True,'port':1,'name':'owned'}).encode()):
            with self.subTest(raw=raw):
                self.vm.session_file.write_bytes(raw)
                with patch('vm_runtime.subprocess.Popen',return_value=self.child) as spawn,patch.object(self.vm,'qmp',return_value={'status':'running'}) as qmp:
                    with self.assertRaises(ValueError):self.vm.start()
                    spawn.assert_not_called();qmp.assert_not_called()

    def test_corrupt_session_is_reported_as_unknown_not_stopped(self):
        self.vm.session_file.write_text('{broken')
        with patch.object(self.vm,'qmp') as qmp:
            result=self.vm.status()
        self.assertEqual(result['state'],'unreachable');self.assertTrue(result['error']);qmp.assert_not_called()

    def test_interrupted_start_claim_blocks_another_guest(self):
        (self.vm.folder/'starting.json').write_text(json.dumps({'name':'moa-owned','port':1234,'started':1}))
        with patch('vm_runtime.subprocess.Popen',return_value=self.child) as spawn,patch.object(self.vm,'qmp',return_value={'status':'running'}):
            with self.assertRaises(ValueError):self.vm.start()
        spawn.assert_not_called()

    def test_session_publication_failure_closes_only_new_owned_child(self):
        def launch(*args,**kwargs):self.vm.session_file.mkdir();return self.child
        with patch('vm_runtime.subprocess.Popen',side_effect=launch),patch.object(self.vm,'qmp',return_value={'status':'running'}):
            with self.assertRaises((ValueError,OSError)):self.vm.start()
        self.child.terminate.assert_called_once();self.child.wait.assert_called();self.assertTrue(self.vm.session_file.is_dir())

    def test_successful_start_publishes_config_and_reuses_same_guest(self):
        def launch(*args,**kwargs):
            self.assertTrue((self.vm.folder/'starting.json').is_file())
            return self.child
        with patch('vm_runtime.subprocess.Popen',side_effect=launch) as spawn,patch.object(self.vm,'qmp',return_value={'status':'running'}):
            self.vm.start();self.vm.start()
        spawn.assert_called_once();self.assertFalse((self.vm.folder/'starting.json').exists())
        self.assertEqual(self.vm.session()['config'],dict(DEFAULT,enabled=True));self.assertEqual(self.vm.session()['pid'],12345)

    def test_failed_launch_clears_owned_start_claim(self):
        with patch('vm_runtime.subprocess.Popen',side_effect=OSError('spawn failed')),patch.object(self.vm,'qmp') as qmp:
            with self.assertRaises(OSError):self.vm.start()
        self.assertFalse((self.vm.folder/'starting.json').exists());qmp.assert_not_called()

    def test_unconfirmed_child_exit_keeps_guard_and_prevents_another_spawn(self):
        def launch(*args,**kwargs):self.vm.session_file.mkdir();return self.child
        self.child.terminate.side_effect=OSError('cannot confirm exit')
        with patch('vm_runtime.subprocess.Popen',side_effect=launch) as spawn,patch.object(self.vm,'qmp'):
            with self.assertRaises((ValueError,OSError)):self.vm.start()
            self.assertTrue((self.vm.folder/'starting.json').exists())
            with self.assertRaises(ValueError):self.vm.start()
        spawn.assert_called_once()

    def test_own_child_timeout_uses_own_kill_handle(self):
        def launch(*args,**kwargs):self.vm.session_file.mkdir();return self.child
        self.child.wait.side_effect=[subprocess.TimeoutExpired('owned',5),0]
        with patch('vm_runtime.subprocess.Popen',side_effect=launch),patch.object(self.vm,'qmp'):
            with self.assertRaises((ValueError,OSError)):self.vm.start()
        self.child.kill.assert_called_once();self.assertFalse((self.vm.folder/'starting.json').exists())

    def test_running_guest_config_mismatch_is_not_reused(self):
        self.vm.session_file.write_text(json.dumps({'pid':12345,'port':1234,'name':'moa-owned','config':dict(DEFAULT,enabled=True,memory_mb=1024)}))
        with patch('vm_runtime.subprocess.Popen') as spawn,patch.object(self.vm,'qmp') as qmp:
            with self.assertRaises(ValueError):self.vm.start()
        spawn.assert_not_called();qmp.assert_called_once_with('query-status')

    def test_shared_session_file_is_not_trusted(self):
        outside=self.root/'outside.json';raw=json.dumps({'pid':12345,'port':1234,'name':'moa-owned','config':dict(DEFAULT,enabled=True)});outside.write_text(raw);os.link(outside,self.vm.session_file)
        with patch('vm_runtime.subprocess.Popen') as spawn,patch.object(self.vm,'qmp') as qmp:
            with self.assertRaises(ValueError):self.vm.start()
        spawn.assert_not_called();qmp.assert_not_called();self.assertEqual(outside.read_text(),raw)

    def test_pending_start_prevents_settings_overwrite(self):
        (self.vm.folder/'starting.json').write_text(json.dumps({'name':'moa-owned','port':1234,'started':1}))
        with self.assertRaises(ValueError):self.vm.save(dict(DEFAULT,enabled=True,memory_mb=1024))
        from vm_runtime import settings
        self.assertEqual(settings(self.store)['memory_mb'],512)

    def test_stop_timeout_preserves_session_and_blocks_duplicate_guest(self):
        record={'pid':12345,'port':1234,'name':'moa-owned','config':dict(DEFAULT,enabled=True)};self.vm.session_file.write_text(json.dumps(record));self.vm.process=self.child
        self.child.wait.side_effect=subprocess.TimeoutExpired('owned',5)
        with patch.object(self.vm,'qmp',return_value={}),patch('vm_runtime.pid_alive',return_value=True),patch('vm_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):self.vm.stop()
            self.assertTrue(self.vm.session_file.exists())
            spawn.assert_not_called()

    def test_dead_guest_does_not_block_new_configuration(self):
        self.vm.session_file.write_text(json.dumps({'pid':111,'port':1234,'name':'moa-old','config':dict(DEFAULT,enabled=True,memory_mb=1024)}))
        calls=0
        def qmp(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:raise OSError('old guest exited')
            return {'status':'running'}
        with patch.object(self.vm,'qmp',side_effect=qmp),patch('vm_runtime.pid_alive',return_value=False),patch('vm_runtime.subprocess.Popen',return_value=self.child) as spawn:self.vm.start()
        spawn.assert_called_once();self.assertEqual(self.vm.session()['config']['memory_mb'],512)

    def test_confirmed_own_exit_clears_only_matching_start_record(self):
        record={'pid':12345,'port':1234,'name':'moa-owned','config':dict(DEFAULT,enabled=True)};self.vm.session_file.write_text(json.dumps(record));self.vm.process=self.child
        (self.vm.folder/'starting.json').write_text(json.dumps({'name':'moa-owned','port':1234,'started':1}))
        with patch.object(self.vm,'qmp',return_value={}):self.assertEqual(self.vm.stop(),{'state':'stopped'})
        self.assertFalse(self.vm.session_file.exists());self.assertFalse((self.vm.folder/'starting.json').exists());self.child.kill.assert_not_called()

    def test_unconfirmed_external_process_exit_preserves_record(self):
        self.vm.session_file.write_text(json.dumps({'pid':12345,'port':1234,'name':'moa-owned','config':dict(DEFAULT,enabled=True)}))
        with patch.object(self.vm,'qmp',return_value={}),patch('vm_runtime.pid_alive',return_value=True),patch('vm_runtime.time.sleep'):
            with self.assertRaises(ValueError):self.vm.stop()
        self.assertTrue(self.vm.session_file.exists())

    def test_dead_unreachable_guest_record_can_be_cleared(self):
        self.vm.session_file.write_text(json.dumps({'pid':12345,'port':1234,'name':'moa-owned','config':dict(DEFAULT,enabled=True)}))
        with patch.object(self.vm,'qmp',side_effect=OSError('already stopped')),patch('vm_runtime.pid_alive',return_value=False):self.assertEqual(self.vm.stop(),{'state':'stopped'})
        self.assertFalse(self.vm.session_file.exists())

    def test_oversized_record_never_reaches_control_connection(self):
        self.vm.session_file.write_bytes(b'x'*65537)
        with patch.object(self.vm,'qmp') as qmp,patch('vm_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):self.vm.start()
        qmp.assert_not_called();spawn.assert_not_called()
