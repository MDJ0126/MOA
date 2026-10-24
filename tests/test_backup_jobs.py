import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from app import Store
from backup_jobs import schedule_request,request_status,list_backups
from data_backup import scheduled_backup,BusyBackup
from supervisor import acquire
from concurrent.futures import ThreadPoolExecutor


class _BackupFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'data')
        self.launch=Mock(return_value=SimpleNamespace(pid=456))
    def tearDown(self):self.temp.cleanup()

class BackupRequestTests(_BackupFixture):
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


class BackupFileBoundaryTests(_BackupFixture):
    def prepare(self):
        first=schedule_request(self.store,self.root,'request-123',123,self.launch)
        archive=self.root/'backups'/first['file'];status=archive.with_suffix('.status.json')
        return archive,status

    def test_shared_status_file_cannot_claim_completion(self):
        archive,status=self.prepare();archive.write_bytes(b'archive')
        outside=self.root/'unrelated-status.json';outside.write_text(json.dumps({'state':'completed'}));os.link(outside,status)
        with self.assertRaises(ValueError):schedule_request(self.store,self.root,'request-123',123,self.launch)
        with self.assertRaises(ValueError):request_status(self.store,self.root,'request-123')
        self.assertEqual(list_backups(self.root)['jobs'],[])
        self.assertEqual(outside.read_text(),json.dumps({'state':'completed'}));self.launch.assert_called_once()

    def test_shared_zip_is_not_a_completed_backup(self):
        archive,status=self.prepare();outside=self.root/'unrelated.zip';outside.write_bytes(b'unrelated');os.link(outside,archive)
        status.write_text(json.dumps({'state':'completed'}))
        with self.assertRaises(ValueError):schedule_request(self.store,self.root,'request-123',123,self.launch)
        with self.assertRaises(ValueError):request_status(self.store,self.root,'request-123')
        self.assertEqual(list_backups(self.root),{'files':[],'jobs':[]})
        self.assertEqual(outside.read_bytes(),b'unrelated');self.launch.assert_called_once()

    def test_status_read_is_bounded_for_replay_too(self):
        archive,status=self.prepare();archive.write_bytes(b'archive');status.write_text(' '*5000+json.dumps({'state':'completed'}))
        with self.assertRaises(ValueError):schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.launch.assert_called_once()

    def test_normal_completion_replay_and_listing_are_consistent(self):
        archive,status=self.prepare();archive.write_bytes(b'preserved archive');status.write_text(json.dumps({'state':'completed','message':'done'}))
        self.assertEqual(schedule_request(self.store,self.root,'request-123',123,self.launch)['state'],'completed')
        self.assertEqual(request_status(self.store,self.root,'request-123')['state'],'completed')
        self.assertEqual(list_backups(self.root)['files'],[archive.name]);self.assertEqual(list_backups(self.root)['jobs'][0]['state'],'completed')
        self.launch.assert_called_once()

    def test_corrupt_status_can_retry_without_touching_original(self):
        archive,status=self.prepare();status.write_bytes(b'\xffbroken')
        with patch('backup_jobs.pid_alive',return_value=False):schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.assertEqual(self.launch.call_count,2);self.assertEqual(status.read_bytes(),b'\xffbroken')
        self.assertEqual(list_backups(self.root)['jobs'],[])

    def test_corrupt_filename_cannot_schedule_another_helper(self):
        archive,status=self.prepare()
        with self.store.connect() as c:c.execute("UPDATE backup_requests SET file='../unrelated.zip'")
        with self.assertRaises(ValueError):schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.launch.assert_called_once()

    def test_invalid_waiting_pid_is_not_used_for_process_lookup(self):
        archive,status=self.prepare();status.write_text(json.dumps({'state':'waiting','helper_pid':2**80}))
        with patch('backup_jobs.pid_alive',return_value=False) as alive:schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.assertTrue(all(type(c.args[0]) is int and 0<c.args[0]<=0xffffffff for c in alive.call_args_list));self.assertEqual(self.launch.call_count,2)

    def test_retried_helper_does_not_inherit_previous_failure(self):
        archive,status=self.prepare();status.write_text(json.dumps({'state':'failed','helper_pid':456,'message':'old failure'}))
        self.launch.return_value=SimpleNamespace(pid=789)
        with patch('backup_jobs.pid_alive',return_value=False):schedule_request(self.store,self.root,'request-123',123,self.launch)
        with patch('backup_jobs.pid_alive',return_value=True):current=request_status(self.store,self.root,'request-123')
        self.assertEqual(current['state'],'waiting');self.assertEqual(current['helper_pid'],789)
        self.assertEqual(list_backups(self.root,self.store)['jobs'],[])

    def test_previous_waiting_helper_cannot_block_new_attempt(self):
        archive,status=self.prepare();status.write_text(json.dumps({'state':'waiting','helper_pid':456}))
        with self.store.connect() as c:c.execute("UPDATE backup_requests SET helper_pid=789")
        self.launch.return_value=SimpleNamespace(pid=999)
        with patch('backup_jobs.pid_alive',side_effect=lambda pid:pid==456):schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.assertEqual(self.launch.call_count,2)

    def test_deeply_nested_status_is_preserved_and_retryable(self):
        archive,status=self.prepare();raw='['*1500+']'*1500;status.write_text(raw)
        with patch('backup_jobs.pid_alive',return_value=False):
            self.assertEqual(request_status(self.store,self.root,'request-123')['state'],'failed')
            schedule_request(self.store,self.root,'request-123',123,self.launch)
        self.assertEqual(status.read_text(),raw);self.assertEqual(list_backups(self.root)['jobs'],[]);self.assertEqual(self.launch.call_count,2)

    def test_recursive_json_decoder_cannot_break_status_recovery(self):
        archive,status=self.prepare();status.write_text('['*1500+']'*1500)
        decoder=json.JSONDecoder();decoder.scan_once=json.scanner.py_make_scanner(decoder)
        with patch('backup_jobs.json.loads',side_effect=lambda raw:decoder.decode(raw.decode() if isinstance(raw,bytes) else raw)),patch('backup_jobs.pid_alive',return_value=False):
            self.assertEqual(request_status(self.store,self.root,'request-123')['state'],'failed')
            self.assertEqual(list_backups(self.root,self.store)['jobs'],[])

    def test_binary_filename_cannot_break_backup_listing(self):
        archive,status=self.prepare()
        with self.store.connect() as c:c.execute('UPDATE backup_requests SET file=?',(b'\xff',))
        self.assertEqual(list_backups(self.root,self.store),{'files':[],'jobs':[]})

    def test_nested_status_message_cannot_break_readback(self):
        archive,status=self.prepare();raw='{"state":"failed","message":'+'['*1200+'0'+']'*1200+'}';status.write_text(raw)
        with patch('backup_jobs.pid_alive',return_value=False):self.assertEqual(request_status(self.store,self.root,'request-123')['state'],'failed')
        self.assertEqual(list_backups(self.root,self.store)['jobs'],[]);self.assertEqual(status.read_text(),raw)
