import json
from pathlib import Path
import tempfile
import unittest
from app import Store
from data_backup import backup,restore
from slack_service import SlackService
from tests.test_setup import FakeWeb,slack_config
from setup_config import save_slack,read_setting


class SlackRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent)
        self.root=Path(self.temp.name);self.store=Store(self.root/'original')
        save_slack(self.store,slack_config());self.service=SlackService(self.store)
        self.tid=self.store.add('restored task','demo')
        self.store.deliver(self.tid,'developer','reviewer',{'summary':'<b>review me</b>'})
        self.service.enqueue({'event_id':'EvRestored','event':{'type':'app_mention','user':'U12345678','channel':'C12345678','text':'restored instruction','ts':'777.1'}},slack_config()['connections'][0])
        archive=self.root/'backup.zip';backup(self.store.root,archive);restore(archive,self.root/'restored')
        self.restored=Store(self.root/'restored');self.recovery=SlackService(self.restored)
        self.recovery.config=slack_config();self.fake=FakeWeb();self.recovery.web={'shared':self.fake}

    def tearDown(self):self.temp.cleanup()

    def test_restore_holds_queues_even_after_connection_reenabled(self):
        self.recovery.drain();self.recovery.flush()
        self.assertEqual(self.fake.posts,[]);self.assertEqual(len(self.restored.snapshot()['tasks']),1)
        self.assertEqual(self.recovery.recovery()['counts'],{'notification':1,'instruction':1})
        self.assertTrue(read_setting(self.restored,'paused',False))
        self.assertEqual(self.service.recovery()['counts'],{'notification':0,'instruction':0})

    def test_selective_resume_then_dismiss_preserves_history(self):
        items=self.recovery.recovery()['items'];note=next(x for x in items if x['kind']=='notification')
        instruction=next(x for x in items if x['kind']=='instruction')
        self.assertEqual(self.recovery.recover('resume',[note])['processed'],1)
        self.recovery.flush();self.assertEqual(len(self.fake.posts),1)
        self.recovery.recover('dismiss',[instruction]);self.recovery.drain()
        self.assertEqual(len(self.restored.snapshot()['tasks']),1)
        with self.restored.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM slack_inbox').fetchone()[0],'dismissed')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_recovery_audit').fetchone()[0],2)
        self.assertTrue(read_setting(self.restored,'paused',False))

    def test_stale_batch_is_rejected_without_partial_release(self):
        items=self.recovery.recovery()['items']
        with self.assertRaises(ValueError):self.recovery.recover('resume',[items[0],{'kind':'instruction','id':'missing'}])
        self.assertEqual(sum(self.recovery.recovery()['counts'].values()),2)
        with self.restored.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_recovery_audit').fetchone()[0],0)

    def test_audit_failure_rolls_back_release(self):
        with self.restored.connect() as c:c.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON slack_recovery_audit BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.recovery.recover('resume',self.recovery.recovery()['items'])
        self.assertEqual(sum(self.recovery.recovery()['counts'].values()),2)

    def test_resumed_instruction_processes_once_and_new_inbox_is_unaffected(self):
        instruction=next(x for x in self.recovery.recovery()['items'] if x['kind']=='instruction')
        self.recovery.recover('resume',[instruction]);self.recovery.drain();self.recovery.drain()
        self.assertEqual(len(self.restored.snapshot()['tasks']),2)
        with self.restored.connect() as c:self.assertEqual(c.execute('SELECT state FROM slack_inbox').fetchone()[0],'done')

    def test_invalid_selection_cannot_release_or_duplicate_items(self):
        item=self.recovery.recovery()['items'][0]
        for action,items in [('delete',[item]),('resume',[]),('resume',[item,item]),('resume',[{'kind':'notification','id':True}]),('resume',[{'kind':'other','id':1}])]:
            with self.subTest(action=action,items=items),self.assertRaises(ValueError):self.recovery.recover(action,items)
        self.assertEqual(sum(self.recovery.recovery()['counts'].values()),2)
