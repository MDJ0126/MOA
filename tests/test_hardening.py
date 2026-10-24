"""Non-screen regressions: no VM launch, input, capture, or real model calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo
from local_model import relevant_history
from setup_config import PRESETS,SettingsConflict,save_team,save_slack,secret_values,slack_settings,protect,write_setting
from tests.test_setup import slack_config

class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.store=Store(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_stale_team_editor_cannot_overwrite_new_settings(self):
        first=dict(copy.deepcopy(PRESETS[1]),expected_updated=0)
        saved=save_team(self.store,first)
        stale=dict(copy.deepcopy(PRESETS[0]),expected_updated=0)
        with self.assertRaises(SettingsConflict):save_team(self.store,stale)
        with self.store.connect() as c:actual=json.loads(c.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()[0])
        self.assertEqual(actual,saved)
    def test_slack_config_and_secret_rollback_together(self):
        original=save_slack(self.store,slack_config());old_secret=secret_values(self.store)
        with self.store.connect() as c:c.executescript("CREATE TRIGGER deny_secret BEFORE INSERT ON settings WHEN NEW.key='slack_secret_blob' BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        changed=slack_config();changed['channel']='C87654321';changed['connections'][0]['bot_token']='xoxb-replacement'
        with self.assertRaises(sqlite3.IntegrityError):save_slack(self.store,changed)
        self.assertEqual(slack_settings(self.store)['channel'],original['channel']);self.assertEqual(secret_values(self.store),old_secret)
    def test_legacy_encrypted_file_is_preserved_after_database_migration(self):
        old={'shared':{'bot_token':'xoxb-legacy','app_token':'xapp-legacy'}}
        path=Path(self.temp.name)/'slack.secrets';encrypted=protect(json.dumps(old).encode());path.write_bytes(encrypted)
        self.assertEqual(secret_values(self.store),old)
        config=slack_config();config['connections'][0].update(bot_token='',app_token='')
        save_slack(self.store,config)
        self.assertEqual(secret_values(self.store),old);self.assertEqual(path.read_bytes(),encrypted)
    def test_unreadable_tokens_do_not_hide_settings_and_explicit_replacement_recovers(self):
        write_setting(self.store,'slack_secret_blob','aW52YWxpZA==')
        self.assertIn('secret_error',slack_settings(self.store))
        with self.assertRaises(OSError):save_slack(self.store,slack_config())
        config=slack_config();config['replace_secrets']=True
        self.assertNotIn('secret_error',save_slack(self.store,config))
    def test_stale_slack_editor_does_not_replace_secret(self):
        first=slack_config();first['expected_updated']=0;save_slack(self.store,first)
        old=secret_values(self.store);second=slack_config();second['expected_updated']=0;second['connections'][0]['bot_token']='xoxb-stale'
        with self.assertRaises(SettingsConflict):save_slack(self.store,second)
        self.assertEqual(secret_values(self.store),old)
    def test_duplicate_submissions_are_one_task_even_concurrently(self):
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:self.store.add('same request','demo',request_key='request-1234'),range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(len(self.store.snapshot()['tasks']),1)
        with self.assertRaises(ValueError):self.store.add('different request','demo',request_key='request-1234')
    def test_cancelled_inflight_response_cannot_handoff_or_package(self):
        tid=self.store.add('cancel in flight','demo');original=Demo.run
        def cancel(backend,task,role,*args):
            if role=='developer':self.store.control('cancel',tid)
            return original(backend,task,role,*args)
        with patch.object(Demo,'run',cancel):Worker(self.store).tick()
        state=self.store.snapshot();self.assertEqual(state['tasks'][0]['status'],'cancelled')
        self.assertFalse(any(m['agent']=='developer' and m['kind']=='handoff' for m in state['messages']))
        self.assertFalse(any(d['sender']=='developer' for d in state['deliveries']))
        self.assertFalse((Path(self.temp.name)/'tasks'/tid/'report.json').exists())
    def test_cancellation_immediately_before_delivery_is_atomic(self):
        tid=self.store.add('cancel race','demo');original=self.store.deliver
        def cancel_before_delivery(task,sender,recipient,content,**kwargs):
            if sender=='developer':self.store.control('cancel',tid,_connection=kwargs.get('_connection'))
            return original(task,sender,recipient,content,**kwargs)
        with patch.object(self.store,'deliver',cancel_before_delivery):Worker(self.store).tick()
        state=self.store.snapshot();self.assertEqual(state['tasks'][0]['status'],'cancelled')
        self.assertFalse(any(m['agent']=='developer' and m['kind']=='handoff' for m in state['messages']))
        self.assertFalse(any(d['sender']=='developer' for d in state['deliveries']))
    def test_manual_retry_renews_budget_and_keeps_old_report(self):
        tid=self.store.add('retry','demo',max_revisions=1)
        folder=Path(self.temp.name)/'tasks'/tid;folder.mkdir(parents=True);(folder/'report.json').write_text('original failure')
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='failed',stage=2,revision=2,failures=3,retry_at=9999999999 WHERE id=?",(tid,))
        self.store.control('retry',tid);task=self.store.snapshot()['tasks'][0]
        self.assertEqual((task['stage'],task['revision'],task['max_revisions'],task['failures'],task['retry_at']),(1,3,4,0,0))
        self.assertEqual((folder/'attempts/r2/report.json').read_text(),'original failure')
    def test_directed_instruction_survives_unrelated_messages(self):
        history=[{'agent':'user','content':json.dumps({'recipient':'builder','summary':'must preserve requirement'})}]
        history += [{'agent':'user','content':json.dumps({'recipient':'checker','summary':str(i)})} for i in range(12)]
        context=relevant_history(history,'developer','builder')
        self.assertEqual([m['summary'] for m in context],['must preserve requirement'])

if __name__=='__main__':unittest.main()
