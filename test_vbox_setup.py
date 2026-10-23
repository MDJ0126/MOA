from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from app import Store
from vm_runtime import VM,BusyVM
from setup_config import read_setting,write_setting
from environment_profiles import recommend
import vbox_setup as vbox
ORIGINAL_EXECUTABLE=vbox.executable

class VBoxPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data');self.vm=VM(self.store)
        (self.root/'install.iso').write_bytes(b'fake ISO; never booted')
        self.calls=[];self.version='7.2.8r12345';self.registered='';self.failure=None
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(vbox,'ROOT',self.root))
        stack.enter_context(patch.object(vbox,'identity',return_value={'os':'windows','architecture':'AMD64'}))
        stack.enter_context(patch.object(vbox,'recommend',side_effect=lambda p:recommend(p,{'os':'Windows','logical_cpus':12,'ram_gib':32,'available_ram_gib':24})))
        stack.enter_context(patch.object(vbox,'executable',return_value='fake/VBoxManage.exe'))
        stack.enter_context(patch.object(vbox.shutil,'disk_usage',return_value=SimpleNamespace(free=300*2**30)))
        stack.enter_context(patch.object(vbox,'command',side_effect=self.command))
        self.request={'profile':{'target_os':'windows','location':'shared_vm','budget_percent':50},'iso':'install.iso','preset':'balanced'}
    def command(self,binary,args):
        self.calls.append(list(args))
        if args[0]==self.failure:raise ValueError('simulated interruption')
        return self.version if args==['--version'] else self.registered if args==['list','vms'] else ''
    def preview(self):return vbox.preview(self.store,self.vm,self.request)

    def test_windows_plan_and_preparation_never_boot_or_control(self):
        plan=self.preview();self.assertTrue(plan['can_prepare'])
        self.assertEqual(plan['preset']['cpus'],2);self.assertGreaterEqual(plan['disk_mb'],65536)
        self.assertIn('--tpm-type',plan['commands'][1]);self.assertIn('2.0',plan['commands'][1])
        record=vbox.prepare(self.store,self.vm,plan['id']);self.assertEqual(record['state'],'prepared');self.assertEqual(record['completed_steps'],6)
        self.assertEqual([args[0] for args in self.calls if args[0] not in ('--version','list')],['createvm','modifyvm','createmedium','storagectl','storageattach','storageattach'])
        self.assertFalse(any(args[0] in ('startvm','controlvm','guestcontrol','unregistervm') for args in self.calls))
        calls=len(self.calls)
        with patch.object(vbox,'build',side_effect=AssertionError('completed replay must not rebuild')):
            self.assertEqual(vbox.prepare(self.store,self.vm,plan['id']),record)
        self.assertEqual(len(self.calls),calls)

    def test_interruption_preserves_record_and_prevents_automatic_replay(self):
        plan=self.preview();self.failure='storagectl'
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        record=read_setting(self.store,'vbox_preparation',{});self.assertEqual(record['state'],'interrupted');self.assertEqual(record['completed_steps'],3)
        calls=len(self.calls)
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        with self.assertRaises(ValueError):self.preview()
        self.assertEqual(len(self.calls),calls)
        with self.vm.exclusive():pass

    def test_missing_version_collision_and_space_checks(self):
        with patch.object(vbox,'executable',return_value=None):self.assertFalse(self.preview()['can_prepare'])
        self.version='7.1.12r12345';self.assertFalse(self.preview()['can_prepare']);self.version='7.2.8r12345'
        plan=self.preview();self.registered='"'+plan['name']+'" {'+plan['id']+'}'
        self.assertFalse(vbox.inspect(plan)['can_prepare']);self.registered=''
        with patch.object(vbox.shutil,'disk_usage',return_value=SimpleNamespace(free=2**30)):
            self.assertFalse(vbox.inspect(plan)['can_prepare'])
        target=self.root/'runtime/vbox'/plan['name'];target.mkdir(parents=True)
        self.assertFalse(vbox.inspect(plan)['can_prepare'])

    def test_changed_iso_host_plan_and_existing_qemu_are_blocked(self):
        plan=self.preview();(self.root/'install.iso').write_bytes(b'changed ISO')
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        plan=self.preview()
        with patch.object(vbox,'identity',return_value={'os':'linux','architecture':'AMD64'}):
            with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,'not-the-plan')
        with patch.object(self.vm,'session',return_value={'pid':123}):
            with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        self.assertFalse(any(args[0]=='createvm' for args in self.calls))

    def test_invalid_paths_architecture_and_target_do_not_make_commands(self):
        for changes in ({'iso':'../outside.iso'},{'iso':'missing.iso'},{'preset':'bad'},{'profile':{'target_os':'macos','location':'shared_vm'}},{'profile':{'location':'remote'}}):
            with self.assertRaises(ValueError):vbox.preview(self.store,self.vm,{**self.request,**changes})
        with patch.object(vbox,'identity',return_value={'os':'macos','architecture':'arm64'}):
            with self.assertRaises(ValueError):self.preview()
        self.assertEqual(self.calls,[])

    def test_shared_lease_and_foreign_prepared_record(self):
        with self.vm.exclusive():
            with self.assertRaises(BusyVM):self.preview()
        plan=self.preview();record=vbox.prepare(self.store,self.vm,plan['id'])
        record['root']='foreign-project';write_setting(self.store,'vbox_preparation',record)
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])

    def test_linux_plan_uses_linux_graphics_without_tpm(self):
        request={**self.request,'profile':{'target_os':'linux','location':'shared_vm'},'unknown_secret':'do not retain'}
        plan=vbox.preview(self.store,self.vm,request)
        self.assertEqual(plan['target_os'],'linux');self.assertIn('vmsvga',plan['commands'][1]);self.assertNotIn('--tpm-type',plan['commands'][1])
        self.assertNotIn('unknown_secret',plan['request'])

    def test_tampered_commands_and_lost_record_write_never_replay(self):
        plan=self.preview();plan['commands'].append(['startvm',plan['id']]);write_setting(self.store,'vbox_plan',plan)
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        self.assertFalse(any(args[0]=='createvm' for args in self.calls))
        plan=self.preview();original=vbox.write_setting;writes=0
        def failing(store,key,value):
            nonlocal writes
            writes+=1
            if writes>1:raise OSError('record write interrupted')
            return original(store,key,value)
        with patch.object(vbox,'write_setting',side_effect=failing):
            with self.assertRaises(OSError):vbox.prepare(self.store,self.vm,plan['id'])
        self.assertEqual(read_setting(self.store,'vbox_preparation',{})['state'],'preparing')
        calls=len(self.calls)
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        self.assertEqual(len(self.calls),calls)

    def test_auto_choice_is_frozen_when_pressure_changes(self):
        self.request['preset']='auto';plan=self.preview()
        self.assertNotEqual(plan['request']['preset'],'auto')
        with patch.object(vbox,'recommend',side_effect=lambda p:recommend(p,{'os':'Windows','logical_cpus':2,'ram_gib':8,'available_ram_gib':1})):
            self.assertEqual(vbox.prepare(self.store,self.vm,plan['id'])['state'],'prepared')

    def test_custom_install_path_requires_actual_absolute_vboxmanage(self):
        binary=self.root/'VBoxManage.exe';binary.write_bytes(b'fake; not executed')
        with patch.dict(vbox.os.environ,{'MOA_VBOXMANAGE':str(binary)}):self.assertEqual(ORIGINAL_EXECUTABLE(),str(binary.resolve()))
        for value in ('relative/VBoxManage.exe',str(self.root/'missing/VBoxManage.exe'),str(self.root/'install.iso')):
            with patch.dict(vbox.os.environ,{'MOA_VBOXMANAGE':value}):
                with self.assertRaises(ValueError):ORIGINAL_EXECUTABLE()

if __name__=='__main__':unittest.main()
