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
        self.calls=[];self.version='7.2.8r12345';self.registered='';self.failure=None;self.iso_type='Windows11_64';self.iso_support='on'
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
        if args[:2]==['unattended','detect']:return 'OSTypeId='+__import__('json').dumps(self.iso_type)+'\nOSVersion="test"\nIsInstallSupported='+self.iso_support+'\nImageIndex1="Test edition"'
        return self.version if args==['--version'] else self.registered if args==['list','vms'] else ''
    def preview(self):return vbox.preview(self.store,self.vm,self.request)

    def test_windows_plan_and_preparation_never_boot_or_control(self):
        plan=self.preview();self.assertTrue(plan['can_prepare'])
        self.assertEqual(plan['preset']['cpus'],2);self.assertGreaterEqual(plan['disk_mb'],65536)
        self.assertIn('--tpm-type',plan['commands'][1]);self.assertIn('2.0',plan['commands'][1])
        record=vbox.prepare(self.store,self.vm,plan['id']);self.assertEqual(record['state'],'prepared');self.assertEqual(record['completed_steps'],6)
        self.assertEqual([args[0] for args in self.calls if args[0] not in ('--version','list','unattended')],['createvm','modifyvm','createmedium','storagectl','storageattach','storageattach'])
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
        self.iso_type='Ubuntu_64'
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

    def test_recovery_clears_only_absent_vm_records_and_replays_receipt(self):
        plan=self.preview();vbox.prepare(self.store,self.vm,plan['id']);self.calls.clear()
        check=vbox.recovery_check(self.store,self.vm);self.assertTrue(check['can_clear'])
        result=vbox.clear_preparation(self.store,self.vm,plan['id'],'recovery-request-01');self.assertTrue(result['cleared'])
        self.assertEqual(read_setting(self.store,'vbox_preparation',{}),{});self.assertEqual(read_setting(self.store,'vbox_plan',{}),{})
        self.assertTrue((self.root/'runtime/vbox-owner.id').exists())
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM vbox_recovery_audit').fetchone()[0],1)
        next_plan=self.preview();vbox.prepare(self.store,self.vm,next_plan['id']);calls=len(self.calls)
        self.assertTrue(vbox.clear_preparation(self.store,self.vm,plan['id'],'recovery-request-01')['replayed'])
        self.assertEqual(read_setting(self.store,'vbox_preparation',{})['id'],next_plan['id'])
        self.assertEqual(len(self.calls),calls)

    def test_recovery_registered_folder_and_unknown_list_preserve_records(self):
        plan=self.preview();vbox.prepare(self.store,self.vm,plan['id']);before=read_setting(self.store,'vbox_preparation',{})
        for listing in ('"owned" {'+plan['id']+'}','unrecognized response'):
            self.registered=listing;self.assertFalse(vbox.recovery_check(self.store,self.vm)['can_clear'])
            with self.assertRaises(ValueError):vbox.clear_preparation(self.store,self.vm,plan['id'],'recovery-blocked-01')
        self.registered='';folder=self.root/'runtime/vbox'/plan['name'];folder.mkdir(parents=True)
        self.assertFalse(vbox.recovery_check(self.store,self.vm)['can_clear']);folder.rmdir()
        self.assertEqual(read_setting(self.store,'vbox_preparation',{}),before)
        with patch.object(vbox,'executable',return_value=None):self.assertFalse(vbox.recovery_check(self.store,self.vm)['can_clear'])
        with self.vm.exclusive():
            with self.assertRaises(BusyVM):vbox.recovery_check(self.store,self.vm)

    def test_restored_or_legacy_record_without_runtime_owner_never_adopts(self):
        plan=self.preview();vbox.prepare(self.store,self.vm,plan['id'])
        marker=self.root/'runtime/vbox-owner.id';marker.unlink()
        check=vbox.recovery_check(self.store,self.vm);self.assertFalse(check['can_clear']);self.assertIn('소유 표식',check['reason'])
        self.assertFalse(marker.exists())
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        with self.assertRaises(ValueError):vbox.clear_preparation(self.store,self.vm,plan['id'],'restore-recovery-01')

    def test_recovery_audit_failure_rolls_back_metadata_clear(self):
        import sqlite3
        plan=self.preview();vbox.prepare(self.store,self.vm,plan['id'])
        with self.store.connect() as c:
            c.execute('CREATE TABLE vbox_recovery_audit(request_key TEXT PRIMARY KEY,record_id TEXT NOT NULL,snapshot TEXT NOT NULL,created REAL NOT NULL)')
            c.execute("CREATE TRIGGER reject_vbox_audit BEFORE INSERT ON vbox_recovery_audit BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):vbox.clear_preparation(self.store,self.vm,plan['id'],'audit-recovery-01')
        self.assertEqual(read_setting(self.store,'vbox_plan',{})['id'],plan['id']);self.assertEqual(read_setting(self.store,'vbox_preparation',{})['id'],plan['id'])

    def test_recovery_detects_changed_record_and_readonly_queries_only(self):
        plan=self.preview();vbox.prepare(self.store,self.vm,plan['id']);self.calls.clear();original=self.command
        def racing(binary,args):
            result=original(binary,args)
            if args==['list','vms']:
                changed=read_setting(self.store,'vbox_plan',{});changed['created']+=1;write_setting(self.store,'vbox_plan',changed)
            return result
        with patch.object(vbox,'command',side_effect=racing):
            with self.assertRaisesRegex(ValueError,'기록이 바뀌었'):vbox.clear_preparation(self.store,self.vm,plan['id'],'racing-recovery-01')
        self.assertEqual(read_setting(self.store,'vbox_preparation',{})['id'],plan['id'])
        self.assertTrue(all(args[0] in ('--version','list') for args in self.calls))

    def test_recovery_permission_failure_and_bad_id_preserve_records(self):
        plan=self.preview();vbox.prepare(self.store,self.vm,plan['id']);target=self.root/'runtime/vbox'/plan['name'];original=Path.lstat
        def denied(path,*args,**kwargs):
            if path==target:raise PermissionError('denied')
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',denied):self.assertFalse(vbox.recovery_check(self.store,self.vm)['can_clear'])
        with self.assertRaises(ValueError):vbox.clear_preparation(self.store,self.vm,'wrong-id','bad-id-recovery')
        with self.assertRaises(ValueError):vbox.clear_preparation(self.store,self.vm,plan['id'],'short')
        self.assertEqual(read_setting(self.store,'vbox_preparation',{})['id'],plan['id'])

    def test_project_lock_covers_different_data_stores_and_releases(self):
        other=Store(self.root/'other-data');other_vm=VM(other)
        with vbox.project_lock():
            with self.assertRaises(BusyVM):vbox.preview(other,other_vm,self.request)
            with other_vm.exclusive():pass
        self.assertTrue(vbox.preview(other,other_vm,self.request)['can_prepare'])

    def test_project_lock_is_exclusive_in_a_separate_python_process(self):
        import subprocess,sys
        script='''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import vbox_setup as vbox
from vm_runtime import BusyVM
vbox.ROOT=Path(sys.argv[2])
try:
 with vbox.project_lock():pass
except BusyVM:raise SystemExit(0)
raise SystemExit(7)
'''
        args=[sys.executable,'-I','-c',script,str(Path(__file__).resolve().parent),str(self.root)]
        with vbox.project_lock():
            result=subprocess.run(args,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        result=subprocess.run(args,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,7,result.stderr.decode(errors='replace'))

    def test_two_data_stores_cannot_prepare_two_vms_after_metadata_loss(self):
        other=Store(self.root/'other-data');other_vm=VM(other)
        first=self.preview();second=vbox.preview(other,other_vm,self.request)
        original=self.command
        def simulate_files(binary,args):
            result=original(binary,args)
            if args[0]=='createvm':
                name=args[args.index('--name')+1];uid=args[args.index('--uuid')+1]
                self.registered='"'+name+'" {'+uid+'}'
                (self.root/'runtime/vbox'/name).mkdir(parents=True)
            return result
        with patch.object(vbox,'command',side_effect=simulate_files):
            vbox.prepare(self.store,self.vm,first['id'])
            with self.assertRaises(ValueError):vbox.prepare(other,other_vm,second['id'])
        self.assertEqual(sum(args[0]=='createvm' for args in self.calls),1)
        with self.store.connect() as c:c.execute("DELETE FROM settings WHERE key IN ('vbox_plan','vbox_preparation')")
        self.assertFalse(self.preview()['can_prepare'])
        self.assertEqual(read_setting(other,'vbox_preparation',{}),{})

    def test_project_registration_namespace_and_unknown_files_are_checked(self):
        plan=self.preview();prefix=vbox.project_prefix(plan['runtime_owner'])
        self.registered='"'+prefix+'elsewhere" {00000000-0000-0000-0000-000000000001}'
        self.assertFalse(vbox.inspect(plan)['can_prepare'])
        self.registered='"different-project" {00000000-0000-0000-0000-000000000002}'
        self.assertTrue(vbox.inspect(plan)['can_prepare'])
        self.registered='unknown output';self.assertFalse(vbox.inspect(plan)['can_prepare'])
        self.registered='';folder=self.root/'runtime/vbox';folder.mkdir();(folder/'unknown.img').write_bytes(b'preserve me')
        self.assertFalse(vbox.inspect(plan)['can_prepare']);self.assertEqual((folder/'unknown.img').read_bytes(),b'preserve me')

    def test_same_size_and_time_iso_replacement_is_blocked(self):
        import os
        plan=self.preview();path=self.root/'install.iso';before=path.stat();raw=path.read_bytes()
        path.write_bytes(b'X'*len(raw));os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        self.assertFalse(any(args[0]=='createvm' for args in self.calls))

    def test_optional_official_checksum_matches_or_blocks_before_cli(self):
        import hashlib
        expected=hashlib.sha256((self.root/'install.iso').read_bytes()).hexdigest()
        self.request['expected_sha256']=expected.upper();plan=self.preview()
        self.assertTrue(plan['official_checksum_verified']);self.assertEqual(plan['iso_sha256'],expected)
        self.calls.clear()
        for bad in ('0'*64,'not-a-hash',True):
            with self.assertRaises(ValueError):vbox.preview(self.store,self.vm,{**self.request,'expected_sha256':bad})
        self.assertEqual(self.calls,[])

    def test_detected_os_architecture_and_changed_metadata_block_prepare(self):
        for os_type in ('Ubuntu_64','Windows11','Windows11_arm64','Solaris_64'):
            self.iso_type=os_type;self.assertFalse(self.preview()['can_prepare'])
        self.iso_type='Windows11_64';plan=self.preview();self.iso_support='off'
        with self.assertRaises(ValueError):vbox.prepare(self.store,self.vm,plan['id'])
        self.assertFalse(any(args[0]=='createvm' for args in self.calls))
        self.iso_type='Ubuntu_64';self.request['profile']['target_os']='linux'
        plan=self.preview();self.assertTrue(plan['can_prepare']);self.assertFalse(plan['iso_detection']['unattended_supported'])

    def test_detection_parser_preserves_editions_and_rejects_ambiguous_output(self):
        raw='OSTypeId="Windows11_64"\nIsInstallSupported=on\nImageIndex2="Pro"\nImageIndex1="Home"'
        result=vbox.iso_metadata(raw,'windows');self.assertEqual([image['index'] for image in result['images']],[1,2])
        for bad in (raw+'\nOSTypeId="Windows11_64"','unknown output','OSTypeId="Solaris_64"'):
            with self.assertRaises(ValueError):vbox.iso_metadata(bad,'windows')
        self.assertIsNone(vbox.iso_metadata('OSTypeId="Windows11_64"','windows')['unattended_supported'])

if __name__=='__main__':unittest.main()
