from pathlib import Path
import tempfile
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import MagicMock,patch
from app import Store
from backup_jobs import request_status,schedule_request
from data_backup import backup,restore,inspect_backup


class RestoredBackupRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.root=Path(self.temp.name)
        self.original=Store(self.root/'original');self.key='backup-before-restore'
        self.launch=MagicMock();self.launch.return_value.pid=12345
        schedule_request(self.original,self.root,self.key,111,launch=self.launch)
        with self.original.connect() as connection:
            self.record=tuple(connection.execute('SELECT request_key,file,state,helper_pid,created FROM backup_requests').fetchone())
        self.archive=self.root/'saved.zip';backup(self.original.root,self.archive)
        self.destination=self.root/'restored';restore(self.archive,self.destination);self.restored=Store(self.destination)
    def tearDown(self):self.temp.cleanup()

    def test_restored_helper_pid_is_not_adopted_as_live(self):
        with patch('backup_jobs.pid_alive',return_value=True):
            self.assertIsNone(request_status(self.restored,self.root,self.key))
            launch=MagicMock();launch.return_value.pid=23456
            result=schedule_request(self.restored,self.root,self.key,222,launch=launch)
        launch.assert_called_once();self.assertEqual(result['state'],'waiting')
        with self.restored.connect() as connection:
            self.assertEqual(connection.execute('SELECT helper_pid FROM backup_requests').fetchone()[0],23456)

    def test_exact_old_record_and_original_db_are_preserved(self):
        with self.restored.connect() as connection:
            row=connection.execute('SELECT request_key,file,state,helper_pid,created FROM backup_restore_archive').fetchone()
            self.assertEqual(tuple(row),self.record)
            self.assertEqual(connection.execute('SELECT count(*) FROM backup_requests').fetchone()[0],0)
        with self.original.connect() as connection:
            self.assertEqual(tuple(connection.execute('SELECT request_key,file,state,helper_pid,created FROM backup_requests').fetchone()),self.record)

    def test_second_restore_keeps_archive_without_duplicate_rows(self):
        archive=self.root/'second.zip';backup(self.destination,archive)
        target=self.root/'twice';restore(archive,target)
        with Store(target).connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM backup_restore_archive').fetchone()[0],1)
            self.assertEqual(connection.execute('SELECT count(*) FROM backup_requests').fetchone()[0],0)

    def test_new_installation_request_is_archived_on_next_restore(self):
        launch=MagicMock();launch.return_value.pid=23456
        schedule_request(self.restored,self.root,'new-installation-request',222,launch=launch)
        archive=self.root/'second.zip';backup(self.destination,archive);target=self.root/'twice';restore(archive,target)
        with Store(target).connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM backup_restore_archive').fetchone()[0],2)
            self.assertEqual(connection.execute('SELECT count(*) FROM backup_requests').fetchone()[0],0)

    def test_binary_legacy_values_are_archived_without_conversion(self):
        with self.original.connect() as connection:
            connection.execute('UPDATE backup_requests SET file=?,state=?,helper_pid=?',(b'legacy-file',b'legacy-state',b'legacy-pid'))
        archive=self.root/'binary.zip';backup(self.original.root,archive);target=self.root/'binary';restore(archive,target)
        with Store(target).connect() as connection:
            self.assertEqual(tuple(connection.execute('SELECT file,state,helper_pid FROM backup_restore_archive').fetchone()),(b'legacy-file',b'legacy-state',b'legacy-pid'))

    def test_archive_write_failure_does_not_publish_or_change_original(self):
        with self.original.connect() as connection:
            connection.execute('CREATE TABLE backup_restore_archive(id INTEGER PRIMARY KEY,restored REAL NOT NULL,request_key,file,state,helper_pid,created)')
            connection.execute("CREATE TRIGGER reject_backup_archive BEFORE INSERT ON backup_restore_archive BEGIN SELECT RAISE(ABORT,'fixture'); END")
        archive=self.root/'blocked.zip';backup(self.original.root,archive);target=self.root/'blocked'
        with self.assertRaises(sqlite3.IntegrityError):restore(archive,target)
        self.assertFalse(target.exists())
        with self.original.connect() as connection:
            self.assertEqual(tuple(connection.execute('SELECT request_key,file,state,helper_pid,created FROM backup_requests').fetchone()),self.record)

    def test_existing_zip_is_preserved_after_request_separation(self):
        archive=self.root/'backups'/self.record[1];archive.write_bytes(b'old archive evidence')
        launch=MagicMock()
        result=schedule_request(self.restored,self.root,self.key,222,launch=launch)
        self.assertEqual(result['state'],'existing');launch.assert_not_called()
        self.assertEqual(archive.read_bytes(),b'old archive evidence')

    def test_cli_restore_publishes_separated_requests(self):
        target=self.root/'cli-restored'
        result=subprocess.run([sys.executable,str(Path(__file__).parent.parent/'data_backup.py'),'restore',str(self.archive),str(target)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        with Store(target).connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM backup_requests').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT helper_pid FROM backup_restore_archive').fetchone()[0],12345)

    def test_preview_does_not_archive_original_requests(self):
        original=self.archive.read_bytes();result=inspect_backup(self.archive)
        self.assertFalse(result['restored']);self.assertEqual(self.archive.read_bytes(),original)
        with self.original.connect() as connection:
            self.assertEqual(connection.execute('SELECT helper_pid FROM backup_requests').fetchone()[0],12345)
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='backup_restore_archive'").fetchone())
