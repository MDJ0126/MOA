import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from app import Store
from data_backup import backup,restore,inspect_backup
from setup_config import read_setting,write_setting
from vm_runtime import VM,DEFAULT


class RestoredVMOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'original')
        write_setting(self.store,'vm',{**DEFAULT,'enabled':True})
    def set_owner(self,raw):
        with self.store.connect() as connection:connection.execute("INSERT OR REPLACE INTO settings VALUES('vm_owner',?)",(raw,))
    def test_restored_owner_is_archived_and_never_reported_as_current(self):
        raw=json.dumps({'task_id':'old-project-task','agent':'old-agent','role':'developer','since':123})
        self.set_owner(raw);archive=self.root/'backup.zip';backup(self.store.root,archive);target=self.root/'restored';restore(archive,target);restored=Store(target);vm=VM(restored)
        with vm.exclusive():self.assertIsNone(vm.status()['owner'])
        with restored.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM settings WHERE key='vm_owner'").fetchone())
            self.assertEqual(connection.execute("SELECT value FROM settings_recovery_archive WHERE key='vm_owner'").fetchone()[0],raw)
        self.assertFalse(read_setting(restored,'vm',{})['enabled'])
        with self.store.connect() as connection:self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='vm_owner'").fetchone()[0],raw)
    def test_corrupt_and_binary_owner_are_preserved_without_blocking_restored_status(self):
        for index,raw in enumerate(('{private owner',b'\xff\x00',None,'null')):
            with self.subTest(raw=repr(raw)):
                self.set_owner(raw);archive=self.root/f'backup-{index}.zip';backup(self.store.root,archive);target=self.root/f'restored-{index}';restore(archive,target);restored=Store(target)
                self.assertFalse(VM(restored).status()['owner_unavailable'])
                with restored.connect() as connection:self.assertEqual(connection.execute("SELECT value FROM settings_recovery_archive WHERE key='vm_owner'").fetchone()[0],raw)
    def test_repeated_restore_does_not_duplicate_owner_archive(self):
        self.set_owner('{old');archive=self.root/'one.zip';backup(self.store.root,archive);target=self.root/'once';restore(archive,target)
        second=self.root/'two.zip';backup(target,second);twice=self.root/'twice';restore(second,twice)
        with Store(twice).connect() as connection:self.assertEqual(connection.execute("SELECT count(*) FROM settings_recovery_archive WHERE key='vm_owner'").fetchone()[0],1)
    def test_archive_failure_never_publishes_restored_data(self):
        self.set_owner('{old')
        with self.store.connect() as connection:
            connection.execute('CREATE TABLE settings_recovery_archive(id INTEGER PRIMARY KEY,key TEXT NOT NULL,value,token TEXT NOT NULL,created REAL NOT NULL)')
            connection.execute("CREATE TRIGGER reject_owner_archive BEFORE INSERT ON settings_recovery_archive BEGIN SELECT RAISE(ABORT,'fixture'); END")
        archive=self.root/'blocked.zip';backup(self.store.root,archive);target=self.root/'restored'
        with self.assertRaises(sqlite3.IntegrityError):restore(archive,target)
        self.assertFalse(target.exists())
        with self.store.connect() as connection:self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='vm_owner'").fetchone()[0],'{old')
    def test_preview_keeps_original_archive_and_owner(self):
        self.set_owner('{old');archive=self.root/'preview.zip';backup(self.store.root,archive);original=archive.read_bytes()
        self.assertFalse(inspect_backup(archive)['restored']);self.assertEqual(archive.read_bytes(),original)
        with self.store.connect() as connection:self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='vm_owner'").fetchone()[0],'{old')
