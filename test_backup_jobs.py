from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from app import Store
from backup_jobs import schedule_request,request_status
from data_backup import scheduled_backup,BusyBackup
from supervisor import acquire
from concurrent.futures import ThreadPoolExecutor


class BackupRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'data')
        self.launch=Mock(return_value=SimpleNamespace(pid=456))
    def tearDown(self):self.temp.cleanup()

    def test_replayed_request_launches_once_and_persists(self):
        first=schedule_request(self.store,self.root,'request-123',123,self.launch)
        with patch('backup_jobs.pid_alive',return_value=True):
            again=schedule_request(self.store,self.root,'request-123',123,self.launch)
            persisted=request_status(Store(self.root/'data'),self.root,'request-123')
        self.assertEqual(first['file'],again['file'])
        self.assertEqual(persisted['file'],first['file'])
        self.launch.assert_called_once()

    def test_concurrent_replays_launch_once(self):
        with patch('backup_jobs.pid_alive',return_value=True),ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:schedule_request(self.store,self.root,'request-123',123,self.launch),range(8)))
        self.assertEqual(len({r['file'] for r in results}),1)
        self.launch.assert_called_once()

    def test_launch_failure_does_not_leave_a_claimed_request(self):
        self.launch.side_effect=OSError('test launch failure')
        with self.assertRaises(OSError):schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.assertIsNone(request_status(self.store,self.root,'request-123'))

    def test_archive_without_completion_status_is_preserved(self):
        first=schedule_request(self.store,self.root,'request-123',123,self.launch)
        archive=self.root/'backups'/first['file'];archive.write_bytes(b'not-yet-verified')
        with patch('backup_jobs.pid_alive',return_value=False):
            self.assertEqual(request_status(self.store,self.root,'request-123')['state'],'existing')
            replay=schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.assertEqual(replay['state'],'existing')
        self.assertEqual(archive.read_bytes(),b'not-yet-verified')
        self.launch.assert_called_once()

    def test_dead_helper_can_be_retried_using_same_filename(self):
        first=schedule_request(self.store,self.root,'request-123',123,self.launch)
        with patch('backup_jobs.pid_alive',return_value=False):
            self.assertEqual(request_status(self.store,self.root,'request-123')['state'],'failed')
            again=schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.assertEqual(again['file'],first['file'])
        self.assertEqual(self.launch.call_count,2)

    def test_same_job_lock_prevents_a_second_helper(self):
        folder=self.root/'backups';folder.mkdir()
        destination=folder/'test.zip'
        with acquire(destination.with_suffix('.job.lock')):
            with self.assertRaises(BusyBackup):scheduled_backup(self.store.root,destination,123,timeout=0)
        self.assertFalse(destination.with_suffix('.status.json').exists())

    def test_invalid_keys_are_rejected(self):
        for key in ('../escape',None,'short','a'*81):
            with self.assertRaises(ValueError):schedule_request(self.store,self.root,key,123,self.launch)
        self.launch.assert_not_called()
