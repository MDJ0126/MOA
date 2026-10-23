from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from app import Store
from setup_config import PRESETS,save_team


class AtomicHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store=Store(self.temp.name);save_team(self.store,PRESETS[1])
    def tearDown(self):self.temp.cleanup()

    def reject(self,kind):
        with self.store.connect() as c:
            c.execute(f"CREATE TRIGGER reject_log BEFORE INSERT ON messages WHEN NEW.kind='{kind}' BEGIN SELECT RAISE(ABORT,'history unavailable'); END")
    def unblock(self):
        with self.store.connect() as c:c.execute('DROP TRIGGER reject_log')

    def test_task_and_initial_instruction_commit_together(self):
        self.reject('instruction')
        for key in (None,'task-atomic-123'):
            with self.assertRaisesRegex(sqlite3.IntegrityError,'history unavailable'):self.store.add('atomic task','demo',request_key=key)
            self.assertEqual(self.store.snapshot()['tasks'],[])
        self.unblock();tid=self.store.add('atomic task','demo',request_key='task-atomic-123')
        again=Store(self.temp.name).add('atomic task','demo',request_key='task-atomic-123')
        self.assertEqual(tid,again)
        self.assertEqual(len([m for m in self.store.snapshot()['messages'] if m['kind']=='instruction']),1)

    def test_cancel_and_pause_roll_back_when_history_cannot_commit(self):
        tid=self.store.add('atomic controls','demo');self.reject('control')
        with self.assertRaises(sqlite3.IntegrityError):self.store.control('cancel',tid)
        with self.assertRaises(sqlite3.IntegrityError):self.store.control('pause')
        state=self.store.snapshot();self.assertEqual(state['tasks'][0]['status'],'queued');self.assertFalse(state['paused'])
        self.unblock();self.store.control('cancel',tid)
        state=self.store.snapshot();self.assertEqual(state['tasks'][0]['status'],'cancelled')
        self.assertEqual([m['content'] for m in state['messages'] if m['kind']=='control'],['cancel'])

    def test_retry_state_and_audit_roll_back_together(self):
        tid=self.store.add('retry atomic','demo')
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='failed',stage=1,error='original failure' WHERE id=?",(tid,))
        before=self.store.snapshot()['tasks'][0];self.reject('control')
        with self.assertRaises(sqlite3.IntegrityError):self.store.control('retry',tid)
        self.assertEqual(self.store.snapshot()['tasks'][0],before)
        self.unblock();self.store.control('retry',tid)
        after=self.store.snapshot()['tasks'][0];self.assertEqual(after['status'],'queued');self.assertEqual(after['revision'],before['revision']+1)

    def test_concurrent_and_restarted_message_replay_is_one_delivery(self):
        tid=self.store.add('messages','demo')
        def send(_):Store(self.temp.name).message(tid,'builder','one instruction',request_key='message-1234')
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(send,range(2)))
        send(None)
        state=self.store.snapshot();self.assertEqual(len(state['deliveries']),1);self.assertEqual(state['outbox_pending'],1)
        self.assertEqual(len([m for m in state['messages'] if m['kind']=='handoff']),1)
        with self.assertRaisesRegex(ValueError,'다른 메시지'):self.store.message(tid,'builder','changed',request_key='message-1234')
        other=self.store.add('other task','demo')
        with self.assertRaises(ValueError):self.store.message(other,'builder','one instruction',request_key='message-1234')

    def test_message_request_rolls_back_with_delivery_and_can_be_retried(self):
        tid=self.store.add('message rollback','demo');self.reject('handoff')
        with self.assertRaises(sqlite3.IntegrityError):self.store.message(tid,'builder','retry safely',request_key='message-rollback')
        state=self.store.snapshot();self.assertEqual(state['deliveries'],[]);self.assertEqual(state['outbox_pending'],0)
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM message_requests').fetchone()[0],0)
        self.unblock();self.store.message(tid,'builder','retry safely',request_key='message-rollback')
        self.assertEqual(len(self.store.snapshot()['deliveries']),1)

    def test_legacy_no_key_and_invalid_keys(self):
        tid=self.store.add('legacy messages','demo')
        for key in ('short','../request',123):
            with self.assertRaises(ValueError):self.store.message(tid,'builder','bad key',request_key=key)
        self.store.message(tid,'builder','separate note');self.store.message(tid,'builder','separate note')
        self.assertEqual(len(self.store.snapshot()['deliveries']),2)

    def test_backup_restore_preserves_message_replay_receipt(self):
        from data_backup import backup,restore
        tid=self.store.add('portable message receipt','demo')
        self.store.message(tid,'builder','saved instruction',request_key='backup-message-123')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            archive=Path(temporary)/'backup.zip';destination=Path(temporary)/'restored'
            backup(self.store.root,archive);restore(archive,destination)
            restored=Store(destination)
            restored.message(tid,'builder','saved instruction',request_key='backup-message-123')
            state=restored.snapshot();self.assertEqual(len(state['deliveries']),1);self.assertEqual(state['outbox_pending'],1);self.assertTrue(state['paused'])
