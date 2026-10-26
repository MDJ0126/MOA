import tempfile,unittest
from pathlib import Path
from app import Store
import desktop_notifications as n

class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup);self.store=Store(self.temp.name)
    def complete(self):
        tid=self.store.add('완료 알림 테스트','demo')
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,))
        return tid
    def test_default_on_completion_claim_ack_and_restart_no_duplicate(self):
        self.assertTrue(n.settings(self.store)['enabled']);tid=self.complete();batch=n.claim(self.store)
        self.assertEqual([v['task_id'] for v in batch['items']],[tid]);self.assertEqual(n.claim(Store(self.temp.name))['items'],[])
        n.acknowledge(self.store,{'token':batch['token'],'success':True})
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT state FROM desktop_notifications').fetchone()[0],'sent')
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,))
        self.assertEqual(n.claim(self.store)['items'],[])
    def test_off_is_persistent_and_does_not_replay_old_completions(self):
        self.complete();n.configure(self.store,{'enabled':False});self.complete()
        self.assertFalse(n.settings(Store(self.temp.name))['enabled']);self.assertEqual(n.claim(self.store)['items'],[])
        n.configure(self.store,{'enabled':True});self.assertEqual(n.claim(self.store)['items'],[])
        self.complete();self.assertEqual(len(n.claim(self.store)['items']),1)
    def test_failed_cancelled_or_rolled_back_completion_does_not_notify(self):
        tid=self.store.add('status test','demo')
        with self.store.connect() as c:
            c.execute("UPDATE tasks SET status='failed' WHERE id=?",(tid,));c.execute("UPDATE tasks SET status='cancelled' WHERE id=?",(tid,))
        with self.store.connect() as c:
            c.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,));c.rollback()
        self.assertEqual(n.claim(self.store)['items'],[])
    def test_installation_does_not_backfill_completed_tasks(self):
        with self.store.connect() as c:c.execute('DROP TRIGGER desktop_task_completed')
        self.complete();n.initialize(self.store);self.assertEqual(n.claim(self.store)['items'],[])
    def test_test_notification_is_explicit_and_requires_on(self):
        n.test(self.store);self.assertEqual(n.claim(self.store)['items'][0]['kind'],'test')
        n.configure(self.store,{'enabled':False})
        with self.assertRaises(ValueError):n.test(self.store)
        with self.assertRaises(ValueError):n.configure(self.store,{'enabled':'false'})
