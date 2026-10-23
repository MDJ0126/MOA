import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from app import Store
from data_backup import backup,restore
from environment_profiles import DEFAULT,settings,recommend


class RestoredProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'original')
        self.tid=self.store.add('preserve frozen task','demo')
        with self.store.connect() as c:self.frozen=c.execute('SELECT execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()[0]

    def restored(self,raw,index):
        with self.store.connect() as c:c.execute("INSERT OR REPLACE INTO settings VALUES('execution_profile',?)",(raw,))
        archive=self.root/f'{index}.zip';backup(self.store.root,archive)
        destination=self.root/f'restored-{index}';restore(archive,destination)
        return Store(destination)

    def test_invalid_profiles_are_archived_and_restore_to_usable_defaults(self):
        for index,raw in enumerate(('{broken','[]','null',b'\xff',None,'{"budget_percent":true}','{"updated":-1}','{"updated":NaN}')):
            with self.subTest(raw=repr(raw)):
                restored=self.restored(raw,index);profile=settings(restored)
                self.assertEqual(profile,{**DEFAULT,'updated':0})
                result=recommend(profile,{'os':'Windows','architecture':'AMD64','ram_gib':16,'available_ram_gib':10,'logical_cpus':8,'cpu_used_percent':0})
                self.assertEqual(result['target_os'],'windows');self.assertFalse(result['connected'])
                with restored.connect() as c:self.assertEqual(c.execute("SELECT value FROM settings_recovery_archive WHERE key='execution_profile'").fetchone()[0],raw)
                with restored.connect() as c:self.assertEqual(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],self.frozen)
                with self.store.connect() as c:self.assertEqual(c.execute("SELECT value FROM settings WHERE key='execution_profile'").fetchone()[0],raw)

    def test_valid_partial_profile_is_preserved_without_connecting(self):
        raw=json.dumps({'target_os':'linux','location':'remote','updated':12})
        restored=self.restored(raw,'valid')
        self.assertEqual(settings(restored),{'target_os':'linux','location':'remote','budget_percent':50,'updated':12})
        self.assertIsNone(recommend(settings(restored),{})['recommended'])

    def test_archive_failure_never_publishes_restored_data(self):
        with self.store.connect() as c:
            c.execute('CREATE TABLE settings_recovery_archive(id INTEGER PRIMARY KEY,key TEXT NOT NULL,value,token TEXT NOT NULL,created REAL NOT NULL)')
            c.execute("CREATE TRIGGER reject_profile_archive BEFORE INSERT ON settings_recovery_archive WHEN NEW.key='execution_profile' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.restored('{broken','blocked')
        self.assertFalse((self.root/'restored-blocked').exists())


if __name__=='__main__':unittest.main()
