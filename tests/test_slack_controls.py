import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from app import Store
from slack_service import SlackService
from setup_config import read_setting
from tests.test_setup import slack_config

class SlackControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.service=SlackService(self.store);self.service.config=slack_config();self.connection=self.service.config['connections'][0]
    def event(self,key,text):return {'event_id':key,'event':{'type':'app_mention','user':'U12345678','channel':'C12345678','text':text,'ts':'500.1','thread_ts':'500.1'}}
    def reject_receipt(self):
        with self.store.connect() as c:c.execute("CREATE TRIGGER reject_control_receipt BEFORE INSERT ON slack_events BEGIN SELECT RAISE(ABORT,'fixture receipt failure'); END")
    def test_pause_and_receipt_failure_roll_back_together(self):
        self.reject_receipt()
        with self.assertRaises(sqlite3.IntegrityError):self.service.receive(self.event('EvPauseAtomic','pause'),self.connection)
        self.assertFalse(read_setting(self.store,'paused',False))
        with self.store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE kind='control'").fetchone()[0],0)
    def test_cancel_delivery_and_receipt_failure_roll_back_together(self):
        tid=self.service.receive(self.event('EvStartAtomic','build fixture'),self.connection)
        with self.store.connect() as c:before=c.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0]
        self.reject_receipt()
        with self.assertRaises(sqlite3.IntegrityError):self.service.receive(self.event('EvCancelAtomic','cancel'),self.connection)
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'queued')
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],before)
    def test_unmapped_control_is_not_a_new_model_task(self):
        for index,text in enumerate(('status','cancel')):self.service.receive(self.event('EvUnmapped'+str(index),text),self.connection)
        self.assertEqual(self.store.snapshot()['tasks'],[])

    def test_committed_pause_replay_does_not_undo_manual_resume(self):
        payload=self.event('EvCommittedPause','pause');self.service.receive(payload,self.connection)
        self.store.control('resume');reopened=SlackService(Store(self.temp.name));reopened.config=slack_config();reopened.receive(payload,self.connection)
        self.assertFalse(read_setting(self.store,'paused',False))
        with self.store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE kind='control' AND content='pause'").fetchone()[0],1)
    def test_two_receivers_apply_the_same_control_once(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from unittest.mock import patch
        other=SlackService(self.store);other.config=slack_config();barrier=threading.Barrier(2)
        first=self.service._control_event;second=other._control_event
        def synchronized(original,*args):barrier.wait(timeout=5);return original(*args)
        with patch.object(self.service,'_control_event',side_effect=lambda *args:synchronized(first,*args)),patch.object(other,'_control_event',side_effect=lambda *args:synchronized(second,*args)),ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(service.receive,self.event('EvConcurrentPause','pause'),self.connection) for service in (self.service,other)]
            for future in futures:future.result(timeout=10)
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE kind='control' AND content='pause'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM slack_events WHERE id='EvConcurrentPause'").fetchone()[0],1)
    def test_cancel_response_uses_current_terminal_status(self):
        tid=self.service.receive(self.event('EvCompletedStart','build fixture'),self.connection)
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,))
        self.service.receive(self.event('EvLateCancel','cancel'),self.connection)
        with self.store.connect() as c:
            self.assertEqual(json.loads(c.execute('SELECT content FROM deliveries ORDER BY id DESC LIMIT 1').fetchone()[0])['summary'],'현재 작업 상태: completed')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE kind='control' AND content='cancel'").fetchone()[0],0)
    def test_resume_receipt_failure_retains_pause_and_response_failure_retains_task(self):
        self.store.control('pause');self.reject_receipt()
        with self.assertRaises(sqlite3.IntegrityError):self.service.receive(self.event('EvResumeFail','resume'),self.connection)
        self.assertTrue(read_setting(self.store,'paused',False))
        with self.store.connect() as c:c.execute('DROP TRIGGER reject_control_receipt')
        tid=self.service.receive(self.event('EvResponseStart','build fixture'),self.connection)
        with self.store.connect() as c:c.execute("CREATE TRIGGER reject_control_response BEFORE INSERT ON deliveries BEGIN SELECT RAISE(ABORT,'fixture response failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.service.receive(self.event('EvResponseFail','cancel'),self.connection)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'queued')
            self.assertIsNone(c.execute("SELECT 1 FROM slack_events WHERE id='EvResponseFail'").fetchone())
    def test_pause_accepts_instructions_into_queue(self):
        self.service.receive(self.event('EvPauseQueue','pause'),self.connection)
        tid=self.service.receive(self.event('EvPausedInstruction','build after resume'),self.connection)
        self.assertTrue(read_setting(self.store,'paused',False))
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'queued')

if __name__=='__main__':unittest.main()
