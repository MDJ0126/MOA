from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store
from vm_runtime import VM
from setup_config import read_setting,write_setting
import vbox_setup as vbox
import vbox_install as install
from private_files import private_directory,security_sddl,current_sid

class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data');self.vm=VM(self.store);self.calls=[];self.quiet=[];self.power='poweroff'
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(vbox,'ROOT',self.root));stack.enter_context(patch.object(install,'ROOT',self.root))
        self.owner=vbox.runtime_owner(create=True);uid='00000000-0000-0000-0000-000000000001';name=vbox.project_prefix(self.owner)+uid[:8]
        self.cfg=self.root/'runtime/vbox'/name/(name+'.vbox');self.cfg.parent.mkdir(parents=True);self.cfg.write_text('fake config')
        iso=self.root/'install.iso';iso.write_bytes(b'fake ISO; never booted');digest,stamp=vbox.iso_fingerprint(iso)
        self.metadata='OSTypeId="Windows11_64"\nIsInstallSupported=on\nImageIndex1="Home"\nImageIndex2="Pro"'
        self.plan={'id':uid,'name':name,'root':str(self.root),'host':vbox.identity(),'runtime_owner':self.owner,'version':'7.2.8r12345','target_os':'windows','request':{'iso':'install.iso'},'iso_sha256':digest,'iso_stamp':stamp,'iso_detection':vbox.iso_metadata(self.metadata,'windows')}
        write_setting(self.store,'vbox_plan',self.plan);write_setting(self.store,'vbox_preparation',{**self.plan,'state':'prepared'})
        stack.enter_context(patch.object(vbox,'executable',return_value='fake/VBoxManage.exe'));stack.enter_context(patch.object(vbox,'command',side_effect=self.command))
        stack.enter_context(patch.object(install,'quiet_command',side_effect=self.execute))
        self.secret='Moa-fixtureInstall42';self.value={'plan_id':uid,'request_key':'install-request-001','username':'moa','password':self.secret,'image_index':2}
    def command(self,binary,args):
        self.calls.append(args)
        if args==['--version']:return '7.2.8r12345'
        if args[:2]==['unattended','detect']:return self.metadata
        if args[0]=='showvminfo':return '\n'.join(key+'='+json.dumps(value) for key,value in {'VMState':self.power,'UUID':self.plan['id'],'CfgFile':str(self.cfg)}.items())
        raise AssertionError(args)
    def execute(self,binary,args):
        self.quiet.append(args)
        self.assertNotIn(self.secret,' '.join(args));self.assertNotIn('startvm',args);self.assertFalse(any(arg.startswith('--start-vm') for arg in args))
        if args[:2]==['unattended','install']:
            path=Path(next(arg.split('=',1)[1] for arg in args if arg.startswith('--user-password-file=')))
            self.assertEqual(path.read_text(encoding='utf-8').strip(),self.secret)
            (path.parent/'install-answer.xml').write_text('fake prepared answer')
    def test_prepares_media_without_boot_or_persisting_password(self):
        state=install.configure(self.store,self.vm,self.value);self.assertEqual(state['state'],'media_prepared')
        self.assertEqual(len(self.quiet),2);self.assertFalse((self.root/state['private_directory']/'credential.txt').exists())
        with self.store.connect() as c:
            raw=json.dumps([tuple(row) for row in c.execute('SELECT * FROM settings')]);self.assertNotIn(self.secret,raw)
        count=len(self.calls);self.assertEqual(install.configure(self.store,self.vm,self.value),state);self.assertEqual(len(self.calls),count)
    def test_wrong_power_iso_edition_and_support_do_not_modify_vm(self):
        for changes in ({'image_index':3},{'password':'short'},{'username':'root'}):
            with self.assertRaises(ValueError):install.configure(self.store,self.vm,{**self.value,**changes})
        self.power='running'
        with self.assertRaises(ValueError):install.configure(self.store,self.vm,self.value)
        self.power='poweroff';self.metadata=self.metadata.replace('=on','=off')
        with self.assertRaises(ValueError):install.configure(self.store,self.vm,self.value)
        self.metadata=self.metadata.replace('=off','=on')
        (self.root/'install.iso').write_bytes(b'changed ISO')
        with self.assertRaises(ValueError):install.configure(self.store,self.vm,self.value)
        self.assertEqual(self.quiet,[])
    def test_failure_removes_credential_and_blocks_automatic_replay(self):
        with patch.object(install,'quiet_command',side_effect=ValueError('do not expose output')):
            with self.assertRaises(ValueError):install.configure(self.store,self.vm,self.value)
        state=read_setting(self.store,'vbox_installation',{});self.assertEqual(state['state'],'interrupted');self.assertFalse((self.root/state['private_directory']/'credential.txt').exists())
        with self.assertRaises(ValueError):install.configure(self.store,self.vm,self.value)
    def test_private_install_files_are_excluded_from_backup(self):
        from data_backup import backup
        import zipfile
        folder=self.store.root/'runtime/vbox-private/fixture';folder.mkdir(parents=True)
        (folder/'answer.xml').write_text(self.secret)
        output=self.root/'backup.zip';backup(self.store.root,output)
        with zipfile.ZipFile(output) as archive:
            self.assertFalse(any('vbox-private' in name for name in archive.namelist()))
    def test_foreign_config_and_initial_journal_failure_do_not_modify_vm(self):
        import sqlite3
        with patch.object(install,'off_configuration',side_effect=ValueError('foreign configuration')):
            with self.assertRaises(ValueError):install.configure(self.store,self.vm,self.value)
        with patch.object(install,'write_setting',side_effect=sqlite3.OperationalError('fixture write failure')):
            with self.assertRaises(sqlite3.OperationalError):install.configure(self.store,self.vm,self.value)
        self.assertEqual(self.quiet,[])
        self.assertFalse(list((self.root/'runtime/vbox-private').glob('*/credential.txt')))
    def test_restored_backup_does_not_reuse_live_runtime_installation_receipt(self):
        from data_backup import backup,restore
        install.configure(self.store,self.vm,self.value)
        archive=self.root/'restore-check.zip';backup(self.store.root,archive)
        destination=self.root/'restored-data';restore(archive,destination)
        restored=Store(destination);restored_vm=VM(restored)
        count=len(self.quiet)
        with self.assertRaises(ValueError):install.configure(restored,restored_vm,self.value)
        self.assertEqual(len(self.quiet),count)
        self.assertIsNone(read_setting(restored,'vbox_installation',None))
        self.assertTrue(self.cfg.is_file())
        self.assertTrue(list((self.root/'runtime/vbox-private').glob('*/install-answer.xml')))
        with self.assertRaises(ValueError):vbox.prepare(restored,restored_vm,self.plan['id'])
        self.assertEqual(len(self.quiet),count)
    def test_private_directory_is_created_with_restricted_permissions(self):
        directory=private_directory(self.root/'permissions')
        if os.name=='nt':
            acl=security_sddl(directory);self.assertIn('D:P',acl);self.assertEqual(acl.count('(A;'),2);self.assertIn(';;;'+current_sid()+')',acl)
        else:self.assertEqual(directory.stat().st_mode&0o077,0)
        with self.assertRaises(OSError):private_directory(directory)
    def test_quiet_runner_never_captures_sensitive_native_output(self):
        from vbox_install import quiet_command
        # The module function is mocked in setUp; call its original implementation.
        with patch('vbox_install.subprocess.run') as run:
            run.return_value.returncode=0
            ORIGINAL_QUIET('fake',['unattended','install','id'])
            self.assertEqual(run.call_args.kwargs['stdout'],install.subprocess.DEVNULL);self.assertEqual(run.call_args.kwargs['stderr'],install.subprocess.DEVNULL)

ORIGINAL_QUIET=install.quiet_command
if __name__=='__main__':unittest.main()
