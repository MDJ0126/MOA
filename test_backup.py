import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from app import Store
from data_backup import backup, restore, inspect_backup
from setup_config import read_setting, write_setting
from supervisor import acquire
from unittest.mock import patch


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
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
