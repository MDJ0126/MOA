import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo
from setup_config import PRESETS,save_team,team_settings,save_slack,slack_settings,prepare_slack
from slack_service import SlackService,test_connection

def slack_config():
    return {'enabled':True,'receive':True,'notify':True,'channel':'C12345678','allowed_users':['U12345678'],
            'connections':[{'id':'shared','agent_id':'shared','bot_token':'xoxb-unit-test-only','app_token':'xapp-unit-test-only'}]}

class FakeWeb:
    def __init__(self):self.posts=[];self.fail=False
    def auth_test(self):return {'ok':True,'team':'Test workspace','user_id':'UBOT12345'}
    def conversations_info(self,**kwargs):return {'channel':{'name':'agent-room'}}
    def conversations_members(self,**kwargs):return {'members':['UBOT12345'],'response_metadata':{}}
    def apps_connections_open(self):return {'ok':True,'url':'wss://not-a-real-connection.invalid'}
    def chat_postMessage(self,**kwargs):
        if self.fail:raise ConnectionError('offline')
        self.posts.append(kwargs);return {'ok':True,'ts':'100.'+str(len(self.posts))}

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.store=Store(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_each_preset_changes_actual_execution_and_agent_identity(self):
        for preset in PRESETS:
            saved=save_team(self.store,preset);self.assertEqual(saved['preset'],preset['id'])
            tid=self.store.add('preset '+preset['id'],'demo');Worker(self.store).tick()
            snapshot=self.store.snapshot();task=next(t for t in snapshot['tasks'] if t['id']==tid)
            self.assertEqual(task['status'],'completed');self.assertEqual(task['stage'],len(preset['pipeline']))
            starts=[m for m in snapshot['messages'] if m['task_id']==tid and m['kind']=='start']
            self.assertEqual(len(starts),len(preset['pipeline']))
            self.assertTrue(all(m['agent'] in {a['id'] for a in preset['agents']} for m in starts))
            report=json.loads((Path(self.temp.name)/'tasks'/tid/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(len(report['team']['agents']),preset['count'])

    def test_team_changes_do_not_change_pending_task_plan(self):
        save_team(self.store,PRESETS[1]);tid=self.store.add('frozen','demo')
        save_team(self.store,PRESETS[0]);Worker(self.store).tick()
        task=next(t for t in self.store.snapshot()['tasks'] if t['id']==tid)
        self.assertEqual(len(json.loads(task['execution_plan'])['agents']),2)
        self.assertEqual(task['stage'],3)

    def test_compact_repair_routes_to_dynamic_developer_stage(self):
        save_team(self.store,PRESETS[0]);tid=self.store.add('compact repair','demo')
        original=Demo.run;seen=[]
        def review(backend,task,role,*args):
            if role=='reviewer' and not seen:seen.append(1);return {'summary':'수정 필요','passed':False}
            return original(backend,task,role,*args)
        with patch.object(Demo,'run',review):
            worker=Worker(self.store);worker.tick()
            task=self.store.snapshot()['tasks'][0];self.assertEqual((task['stage'],task['revision']),(0,1))
            worker.tick()
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'completed')

    def test_invalid_team_cannot_remove_verification_or_role_coverage(self):
        bad=copy.deepcopy(PRESETS[1]);bad['agents'][1]['capabilities']=['reporter']
        with self.assertRaises(ValueError):save_team(self.store,bad)
        bad=copy.deepcopy(PRESETS[1]);bad['pipeline']=['developer']
        with self.assertRaises(ValueError):save_team(self.store,bad)

    def test_tokens_are_encrypted_masked_and_blank_input_preserves_them(self):
        saved=save_slack(self.store,slack_config())
        self.assertTrue(saved['connections'][0]['has_bot_token'])
        self.assertNotIn('xoxb-',json.dumps(saved))
        with self.store.connect() as c:encrypted=c.execute("SELECT value FROM settings WHERE key='slack_secret_blob'").fetchone()[0]
        self.assertNotIn('xoxb-',encrypted)
        value=slack_config();value['connections'][0].update(bot_token='',app_token='')
        save_slack(self.store,value)
        self.assertEqual(slack_settings(self.store,True)['connections'][0]['bot_token'],'xoxb-unit-test-only')

    def test_slack_test_only_checks_identity_membership_and_socket_permission(self):
        fake=FakeWeb();result=test_connection(self.store,slack_config(),lambda token:fake)
        self.assertTrue(result['ok']);self.assertEqual(fake.posts,[])
        self.assertFalse(slack_settings(self.store)['enabled'])

    def test_unknown_user_and_bots_cannot_create_work_and_events_are_deduplicated(self):
        service=SlackService(self.store);service.config=slack_config();connection=service.config['connections'][0]
        base={'event_id':'Ev1','event':{'type':'app_mention','user':'UOTHER123','channel':'C12345678','text':'do work','ts':'100.1'}}
        self.assertIsNone(service.receive(base,connection));self.assertEqual(self.store.snapshot()['tasks'],[])
        good=copy.deepcopy(base);good['event_id']='Ev2';good['event']['user']='U12345678'
        tid=service.receive(good,connection);self.assertIsNotNone(tid)
        service.receive(good,connection);self.assertEqual(len(self.store.snapshot()['tasks']),1)
        own=copy.deepcopy(good);own['event_id']='Ev3';own['event']['bot_id']='B123'
        service.receive(own,connection);self.assertEqual(len(self.store.snapshot()['tasks']),1)

    def test_durable_inbox_and_same_slack_thread_for_all_handoffs(self):
        service=SlackService(self.store);service.config=slack_config();fake=FakeWeb();service.web={'shared':fake}
        connection=service.config['connections'][0]
        event={'event_id':'EvInbox','event':{'type':'app_mention','user':'U12345678','channel':'C12345678','text':'build','ts':'200.1'}}
        service.enqueue(event,connection)
        second=SlackService(self.store);second.config=service.config;second.web=service.web;second.drain()
        task=self.store.snapshot()['tasks'][0]
        self.store.deliver(task['id'],'developer','reviewer',{'summary':'done'})
        second.flush();self.assertTrue(fake.posts)
        self.assertTrue(all(p['thread_ts']=='200.1' for p in fake.posts))
        second.drain();self.assertEqual(len(self.store.snapshot()['tasks']),1)

    def test_ui_jobs_notify_new_channel_thread_but_old_history_is_not_sent(self):
        old=self.store.add('old local work','demo');self.store.deliver(old,'planner','developer',{'summary':'old'})
        save_slack(self.store,slack_config())
        new=self.store.add('new local work','demo')
        self.store.deliver(new,'planner','developer',{'summary':'plan'})
        self.store.deliver(new,'developer','reviewer',{'summary':'code'})
        service=SlackService(self.store);service.config=slack_settings(self.store,True);fake=FakeWeb();service.web={'shared':fake};service.flush()
        self.assertEqual(len(fake.posts),2);self.assertIsNone(fake.posts[0]['thread_ts'])
        self.assertEqual(fake.posts[1]['thread_ts'],'100.1')
        self.assertTrue(all(old not in p['text'] for p in fake.posts))

    def test_source_key_prevents_duplicate_task_after_partial_ingress(self):
        first=self.store.add('event','demo',source_key='slack:one')
        second=self.store.add('event','demo',source_key='slack:one')
        self.assertEqual(first,second);self.assertEqual(len(self.store.snapshot()['tasks']),1)

    def test_slack_instruction_replay_after_commit_and_task_completion(self):
        service=SlackService(self.store);service.config=slack_config();connection=service.config['connections'][0]
        def event(eid,text):return {'event_id':eid,'event':{'type':'app_mention','user':'U12345678','channel':'C12345678','text':text,'ts':'610.1','thread_ts':'610.1'}}
        tid=service.receive(event('EvInitial','build'),connection)
        original=self.store.message
        def committed_then_crashed(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('simulated crash before event acknowledgement')
        payload=event('EvInstruction','keep the output concise')
        with patch.object(self.store,'message',committed_then_crashed),self.assertRaises(RuntimeError):service.receive(payload,connection)
        with self.store.connect() as c:
            count=c.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0]
            c.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,))
        restarted=SlackService(Store(self.temp.name));restarted.config=slack_config()
        self.assertEqual(restarted.receive(payload,connection),tid)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],count)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)

    def test_slack_delivery_ids_survive_reopen_and_differ_between_stores(self):
        def send(store):
            service=SlackService(store);service.config=slack_config();fake=FakeWeb();service.web={'shared':fake};service.flush()
            return [p['client_msg_id'] for p in fake.posts]
        def prepare(store):
            save_slack(store,slack_config());tid=store.add('delivery identity','demo')
            store.deliver(tid,'developer','reviewer',{'summary':'check'})
        prepare(self.store);first=send(self.store);self.assertEqual(len(first),1)
        with self.store.connect() as c:c.execute("UPDATE outbox SET state='pending'")
        self.assertEqual(send(Store(self.temp.name)),first)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as other:
            store=Store(other);prepare(store)
            self.assertNotEqual(send(store),first)

    def test_slack_namespace_is_preserved_by_backup_restore(self):
        from data_backup import backup,restore
        original=SlackService(self.store).delivery_namespace
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as other:
            archive=Path(other)/'backup.zip';destination=Path(other)/'restored'
            backup(self.temp.name,archive);restore(archive,destination)
            self.assertEqual(SlackService(Store(destination)).delivery_namespace,original)

    def test_malformed_team_values_are_rejected_without_attribute_errors(self):
        for value in ([],None,{'agents':[None]},dict(PRESETS[0],pipeline=[{}]),dict(PRESETS[0],agents=[{'id':'maker','name':None}])):
            with self.subTest(value=value),self.assertRaises(ValueError):save_team(self.store,value)
        with self.assertRaises(ValueError):self.store.add(None,'demo')
        for value in ([],{'channel':None},{'enabled':'false'},{'allowed_users':[None]},{'connections':[None]},{'connections':[{'id':'shared','bot_token':None}]}):
            with self.subTest(slack=value),self.assertRaises(ValueError):prepare_slack(self.store,value)

    def test_slack_cancel_reports_new_status_and_repeated_cancel_is_safe(self):
        service=SlackService(self.store);service.config=slack_config();connection=service.config['connections'][0]
        def event(eid,text):return {'event_id':eid,'event':{'type':'app_mention','user':'U12345678','channel':'C12345678','text':text,'ts':'300.1','thread_ts':'300.1'}}
        tid=service.receive(event('EvStart','build'),connection)
        service.receive(event('EvCancel','cancel'),connection)
        service.receive(event('EvCancelAgain','cancel'),connection)
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'cancelled')
        with self.store.connect() as c:rows=c.execute('SELECT content FROM deliveries WHERE task_id=? ORDER BY id DESC LIMIT 2',(tid,)).fetchall()
        self.assertTrue(all(json.loads(r[0])['summary'].endswith('cancelled') for r in rows))

    def test_slack_actor_and_thread_are_visible_at_task_publication(self):
        team=copy.deepcopy(PRESETS[1])
        for agent in team['agents']:agent['capabilities']=['planner','developer','reviewer','reporter']
        save_team(self.store,team)
        service=SlackService(self.store);service.config=slack_config();connection=dict(service.config['connections'][0],agent_id='checker')
        original=self.store.add;observed=[]
        def inspect_commit(*args,**kwargs):
            tid=original(*args,**kwargs)
            with self.store.connect() as c:
                row=c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()
                thread=c.execute('SELECT thread_ts FROM slack_threads WHERE task_id=?',(tid,)).fetchone()
                instructions=c.execute("SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='instruction'",(tid,)).fetchone()[0]
            self.assertEqual(set(json.loads(row[0])['assignment'].values()),{'checker'})
            self.assertEqual(thread[0],'400.1');self.assertEqual(instructions,1);observed.append(tid)
            return tid
        with patch.object(self.store,'add',inspect_commit):
            service.receive({'event_id':'EvAtomic','event':{'type':'app_mention','user':'U12345678','channel':'C12345678','text':'build','ts':'400.1'}},connection)
        self.assertEqual(len(observed),1)

if __name__=='__main__':unittest.main()
