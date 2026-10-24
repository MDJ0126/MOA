import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
from app import Store
from data_backup import backup,restore,inspect_backup
from setup_config import read_setting,write_setting
from vm_runtime import VM,DEFAULT


class RestoredConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'original')
    def raw(self,key,raw):
        with self.store.connect() as connection:connection.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,raw))
    def restore_case(self,index):
        archive=self.root/f'backup-{index}.zip';backup(self.store.root,archive);target=self.root/f'restored-{index}'
        with patch('setup_config.protect',side_effect=AssertionError('restore must not decrypt tokens')):restore(archive,target)
        return Store(target)
    def test_corrupt_optional_settings_restore_disabled_and_preserve_exact_originals(self):
        for index,raw in enumerate(('{private','[]','null',b'\xff',None)):
            with self.subTest(raw=repr(raw)):
                for key in ('slack','vm'):self.raw(key,raw)
                restored=self.restore_case(index)
                for key in ('slack','vm'):
                    self.assertFalse(read_setting(restored,key,{})['enabled'])
                    with restored.connect() as connection:self.assertEqual(connection.execute('SELECT value FROM settings_recovery_archive WHERE key=?',(key,)).fetchone()[0],raw)
                    with self.store.connect() as connection:self.assertEqual(connection.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()[0],raw)
                self.assertEqual(VM(restored).status()['state'],'stopped')
                self.assertEqual(read_setting(restored,'slack',{})['connections'],[])
    def test_valid_legacy_partial_settings_are_completed_and_secrets_unchanged(self):
        slack={'enabled':True,'channel':'C12345678','connections':[{'id':'shared','agent_id':'shared'}],'allowed_users':['U12345678']}
        write_setting(self.store,'slack',slack);write_setting(self.store,'vm',{'enabled':True})
        write_setting(self.store,'slack_secret_blob','original opaque cipher');write_setting(self.store,'slack_namespace','original-namespace')
        restored=self.restore_case('legacy');config=read_setting(restored,'slack',{})
        self.assertFalse(config['enabled']);self.assertEqual(config['connections'],slack['connections']);self.assertTrue(config['receive'])
        self.assertEqual(read_setting(restored,'slack_secret_blob',None),'original opaque cipher');self.assertEqual(read_setting(restored,'slack_namespace',None),'original-namespace')
        self.assertEqual(VM(restored).status()['config']['memory_mb'],DEFAULT['memory_mb'])
    def test_invalid_fields_are_not_kept_as_live_config(self):
        for index,(slack,vm) in enumerate((({'enabled':'yes'},{**DEFAULT,'cpus':True}),({'connections':{}},{**DEFAULT,'iso':None}),({'updated':float('nan')},{**DEFAULT,'memory_mb':-1}))):
            with self.subTest(index=index):
                write_setting(self.store,'slack',slack);write_setting(self.store,'vm',vm);restored=self.restore_case(index)
                self.assertEqual(read_setting(restored,'slack',{})['connections'],[])
                self.assertEqual(VM(restored).status()['config']['memory_mb'],DEFAULT['memory_mb'])
                json.dumps(read_setting(restored,'slack',{}),allow_nan=False);json.dumps(VM(restored).status(),allow_nan=False)

    def test_connection_archive_failure_preserves_original_and_does_not_publish(self):
        self.raw('slack','{old Slack');self.raw('vm','{old VM')
        with self.store.connect() as connection:
            connection.execute('CREATE TABLE settings_recovery_archive(id INTEGER PRIMARY KEY,key TEXT NOT NULL,value,token TEXT NOT NULL,created REAL NOT NULL)')
            connection.execute("CREATE TRIGGER reject_connection_archive BEFORE INSERT ON settings_recovery_archive BEGIN SELECT RAISE(ABORT,'fixture'); END")
        archive=self.root/'blocked.zip';backup(self.store.root,archive)
        with self.assertRaises(sqlite3.IntegrityError):restore(archive,self.root/'restored')
        self.assertFalse((self.root/'restored').exists())
        with self.store.connect() as connection:self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='slack'").fetchone()[0],'{old Slack')

    def test_preview_of_corrupt_optional_settings_preserves_source_and_zip(self):
        self.raw('slack','{old Slack');self.raw('vm','{old VM');archive=self.root/'preview.zip';backup(self.store.root,archive);original=archive.read_bytes()
        result=inspect_backup(archive);self.assertFalse(result['restored']);self.assertEqual(result['tasks'],0);self.assertEqual(archive.read_bytes(),original)
        with self.store.connect() as connection:self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='vm'").fetchone()[0],'{old VM')
