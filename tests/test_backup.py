import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from app import Store,Worker
from data_backup import backup, restore, inspect_backup
from setup_config import read_setting, write_setting
from supervisor import acquire
from unittest.mock import patch


class BackupTests(unittest.TestCase):
    def test_restored_queue_waits_for_resume_and_executes_once(self):
        tid=self.store.add('queued before backup','demo')
        with self.store.connect() as connection:
            frozen=connection.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0]
        backup(self.source,self.archive);destination=self.root/'restored';restore(self.archive,destination)
        restored=Store(destination);worker=Worker(restored,demo_only=True)
        self.assertFalse(worker.tick());self.assertFalse((destination/'tasks'/tid).exists())
        with restored.connect() as connection:
            self.assertEqual(connection.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0],frozen)
            self.assertEqual(connection.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'queued')
        restored.control('resume');self.assertTrue(worker.tick());self.assertFalse(worker.tick())
        with restored.connect() as connection:
            self.assertEqual(connection.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'completed')
            roles=[row[0] for row in connection.execute("SELECT capability FROM messages WHERE task_id=? AND kind='start' ORDER BY seq",(tid,))]
        self.assertEqual(roles,json.loads(frozen)['pipeline'])
        self.assertTrue((destination/'tasks'/tid/'artifacts.zip').is_file())
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent)
        self.root = Path(self.temp.name)
        self.source = self.root / 'original'
        self.store = Store(self.source)
        write_setting(self.store, 'slack', {'enabled': True})
        write_setting(self.store, 'vm', {'enabled': True})
        (self.source / 'model.key').write_text('do-not-export')
        (self.source / 'tasks').mkdir()
        (self.source / 'tasks' / 'result.txt').write_text('result')
        self.archive = self.root / 'backup.zip'

    def tearDown(self):self.temp.cleanup()

    def test_roundtrip_starts_paused_without_external_connections(self):
        backup(self.source, self.archive)
        destination = self.root / 'restored'
        restore(self.archive, destination)
        restored = Store(destination)
        self.assertTrue(read_setting(restored, 'paused', False))
        for key in ('slack', 'vm'):self.assertFalse(read_setting(restored, key, {})['enabled'])
        self.assertEqual((destination / 'tasks' / 'result.txt').read_text(), 'result')
        self.assertFalse((destination / 'model.key').exists())

    def test_restore_archives_exact_preparation_records_and_retains_archive_history(self):
        from vbox_setup import restoration_status
        records={'vbox_plan':'{"id":"old","root":"old-project"}','vbox_preparation':'invalid legacy JSON','vbox_installation':'{"state":"media_prepared"}'}
        with self.store.connect() as c:
            for key,value in records.items():c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,value))
        backup(self.source,self.archive);destination=self.root/'restored';restore(self.archive,destination)
        restored=Store(destination)
        with restored.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'vbox_%'").fetchone()[0],0)
            self.assertEqual(json.loads(c.execute('SELECT snapshot FROM vbox_restore_archive').fetchone()[0]),records)
        status=restoration_status(restored);self.assertEqual(status['archived_records'],1);self.assertNotIn('snapshot',status)
        with self.store.connect() as c:self.assertEqual(dict(c.execute("SELECT key,value FROM settings WHERE key LIKE 'vbox_%'")),records)
        second=self.root/'second.zip';backup(destination,second);twice=self.root/'restored-twice';restore(second,twice)
        self.assertEqual(restoration_status(Store(twice))['archived_records'],1)
        write_setting(restored,'vbox_plan',{'id':'new-plan'});third=self.root/'third.zip';backup(destination,third);thrice=self.root/'restored-thrice';restore(third,thrice)
        self.assertEqual(restoration_status(Store(thrice))['archived_records'],2)
    def test_restore_archive_failure_does_not_publish_destination(self):
        write_setting(self.store,'vbox_plan',{'id':'old'})
        with self.store.connect() as c:
            c.execute('CREATE TABLE vbox_restore_archive(id INTEGER PRIMARY KEY,restored REAL NOT NULL,snapshot TEXT NOT NULL)')
            c.execute("CREATE TRIGGER reject_restored_vbox BEFORE INSERT ON vbox_restore_archive BEGIN SELECT RAISE(ABORT,'fixture'); END")
        backup(self.source,self.archive);destination=self.root/'restored'
        with self.assertRaises(sqlite3.IntegrityError):restore(self.archive,destination)
        self.assertFalse(destination.exists());self.assertEqual(read_setting(self.store,'vbox_plan',{})['id'],'old')

    def test_binary_preparation_history_survives_preview_restore_and_rebackup(self):
        records={'vbox_plan':b'\xff\x00legacy','vbox_preparation':None,'vbox_installation':'42'}
        with self.store.connect() as c:
            for key,value in records.items():c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,value))
        backup(self.source,self.archive);original=self.archive.read_bytes()
        self.assertFalse(inspect_backup(self.archive)['restored'])
        self.assertEqual(self.archive.read_bytes(),original)
        destination=self.root/'restored';restore(self.archive,destination)
        restored=Store(destination)
        with restored.connect() as c:
            snapshot=json.loads(c.execute('SELECT snapshot FROM vbox_restore_archive').fetchone()[0])
            self.assertEqual(bytes.fromhex(snapshot['vbox_plan']['hex']),records['vbox_plan'])
            self.assertEqual(snapshot['vbox_plan']['sqlite_type'],'blob')
            self.assertIsNone(snapshot['vbox_preparation']);self.assertEqual(snapshot['vbox_installation'],'42')
            self.assertEqual(c.execute("SELECT count(*) FROM settings WHERE key LIKE 'vbox_%'").fetchone()[0],0)
        with self.store.connect() as c:self.assertEqual(dict(c.execute("SELECT key,value FROM settings WHERE key LIKE 'vbox_%'")),records)
        second=self.root/'second.zip';backup(destination,second);twice=self.root/'twice';restore(second,twice)
        with Store(twice).connect() as c:
            rows=c.execute('SELECT snapshot FROM vbox_restore_archive').fetchall()
            self.assertEqual(len(rows),1);self.assertEqual(json.loads(rows[0][0]),snapshot)

    def test_preview_validates_without_replacing_source(self):
        backup(self.source, self.archive)
        original=self.archive.read_bytes()
        result=inspect_backup(self.archive)
        self.assertEqual(result['tasks'],0)
        self.assertFalse(result['restored'])
        self.assertEqual(self.archive.read_bytes(),original)
        self.assertTrue(read_setting(self.store,'slack',{})['enabled'])

    def test_scheduled_backup_waits_then_completes(self):
        from data_backup import scheduled_backup
        with patch('vm_runtime.pid_alive',side_effect=[True,False]), patch('data_backup.time.sleep'):
            scheduled_backup(self.source,self.archive,123)
        status=json.loads(self.archive.with_suffix('.status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['state'],'completed')

    def test_scheduled_timeout_preserves_source(self):
        from data_backup import scheduled_backup
        with self.assertRaisesRegex(ValueError,'초과'):scheduled_backup(self.source,self.archive,123,timeout=0)
        self.assertFalse(self.archive.exists())
        self.assertEqual(json.loads(self.archive.with_suffix('.status.json').read_text(encoding='utf-8'))['state'],'failed')

    def test_running_worker_blocks_backup(self):
        with acquire(self.source / 'worker.lock'):
            with self.assertRaisesRegex(ValueError, '정상 종료'):backup(self.source, self.archive)
        self.assertFalse(self.archive.exists())

    def test_existing_data_and_archive_are_never_overwritten(self):
        backup(self.source, self.archive)
        with self.assertRaises(ValueError):backup(self.source, self.archive)
        with self.assertRaises(ValueError):restore(self.archive, self.source)
        self.assertEqual((self.source / 'model.key').read_text(), 'do-not-export')

    def write_archive(self, name, content, expected_hash=None):
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('data/' + name, content)
            z.writestr('manifest.json', json.dumps({'version': 1, 'files': {name: {'size': len(content), 'sha256': expected_hash or hashlib.sha256(content).hexdigest()}}}))

    def test_path_escape_and_windows_aliases_leave_no_destination(self):
        for name in ('../outside', '/absolute', 'C:/drive', 'CON.txt', 'nested/../file', 'file.', 'a//b'):
            with self.subTest(name=name):
                self.write_archive(name, b'bad')
                with self.assertRaises(ValueError):restore(self.archive, self.root / 'new')
                self.assertFalse((self.root / 'new').exists())

    def test_tamper_and_size_limit_leave_no_destination(self):
        self.write_archive('state.sqlite3', b'not-a-db', 'bad-hash')
        with self.assertRaisesRegex(ValueError, '검증'):restore(self.archive, self.root / 'new')
        with self.assertRaisesRegex(ValueError, '크기'):restore(self.archive, self.root / 'new', max_bytes=1)
        self.assertFalse((self.root / 'new').exists())

    def test_valid_hash_does_not_allow_invalid_database(self):
        self.write_archive('state.sqlite3', b'not-a-database')
        with self.assertRaises(sqlite3.DatabaseError):restore(self.archive, self.root / 'new')
        self.assertFalse((self.root / 'new').exists())

    def test_running_supervisor_blocks_backup(self):
        with acquire(self.source / 'supervisor.lock'):
            with self.assertRaisesRegex(ValueError, '정상 종료'):backup(self.source, self.archive)
        self.assertFalse(self.archive.exists())

    def test_wal_commits_are_in_consistent_snapshot(self):
        with closing(sqlite3.connect(self.source / 'state.sqlite3')) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT OR REPLACE INTO settings VALUES('snapshot_marker','123')")
            writer.commit()
            self.assertTrue((self.source / 'state.sqlite3-wal').exists())
            backup(self.source,self.archive)
            with zipfile.ZipFile(self.archive) as z:
                self.assertFalse(any(n.endswith(('-wal','-shm')) for n in z.namelist()))
            destination=self.root/'restored'
            restore(self.archive,destination)
            self.assertEqual(read_setting(Store(destination),'snapshot_marker',None),123)

    def test_data_io_is_streamed(self):
        payload=b'bounded-memory\x00'*200000
        (self.source/'tasks'/'large.bin').write_bytes(payload)
        original=zipfile.ZipFile.read
        def manifest_only(archive,name,*args,**kwargs):
            self.assertEqual(name,'manifest.json')
            return original(archive,name,*args,**kwargs)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('whole-file read')),patch.object(zipfile.ZipFile,'read',manifest_only):
            backup(self.source,self.archive)
            restore(self.archive,self.root/'restored')
        self.assertEqual((self.root/'restored'/'tasks'/'large.bin').read_bytes(),payload)

    def test_hardlinks_are_rejected(self):
        external=self.root/'private.txt';external.write_text('private')
        os.link(external,self.source/'tasks'/'linked.txt')
        with self.assertRaisesRegex(ValueError,'하드링크'):backup(self.source,self.archive)
        self.assertFalse(self.archive.exists())

    def test_case_insensitive_exclusions(self):
        (self.source/'model.key').rename(self.source/'MODEL.KEY')
        (self.source/'VM').mkdir();(self.source/'VM'/'private.txt').write_text('private')
        backup(self.source,self.archive)
        with zipfile.ZipFile(self.archive) as z:
            self.assertFalse(any(n.casefold()=='data/model.key' or n.casefold().startswith('data/vm/') for n in z.namelist()))
        for name in ('MODEL.KEY','VM/private.txt','STATE.SQLITE3-WAL','Supervisor.JSON'):
            self.write_archive(name,b'private')
            with self.assertRaisesRegex(ValueError,'경로'):restore(self.archive,self.root/'new')
            self.assertFalse((self.root/'new').exists())

    def test_directory_case_alias_is_rejected(self):
        files={'Folder/a':b'a','folder/b':b'b'}
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,content in files.items():z.writestr('data/'+name,content)
            z.writestr('manifest.json',json.dumps({'version':1,'files':{n:{'size':len(c),'sha256':hashlib.sha256(c).hexdigest()} for n,c in files.items()}}))
        with self.assertRaisesRegex(ValueError,'충돌'):restore(self.archive,self.root/'new')
        self.assertFalse((self.root/'new').exists())
