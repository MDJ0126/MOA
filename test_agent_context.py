import io
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
from app import Store,Worker,LocalFiles,Ollama
from local_model import Llama,relevant_history
from setup_config import PRESETS,save_team
from setup_config import make_plan
import copy
from conversation import task_messages


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store=Store(self.temp.name)
        save_team(self.store,PRESETS[1])
    def tearDown(self):self.temp.cleanup()

    def test_file_generation_receives_addressed_instruction_and_shared_plan(self):
        files=LocalFiles(Path(self.temp.name)/'workspace')
        task={'title':'write file','required_files':'["value.py"]','agent':{'id':'builder','instructions':'PREFERENCE_MARKER'}}
        history=[{'agent':'user','content':json.dumps({'recipient':'builder','summary':'KEEP_MARKER'})},
                 {'agent':'user','content':json.dumps({'recipient':'checker','summary':'EXCLUDE_MARKER'})},
                 {'agent':'builder','recipient':'builder','content':json.dumps({'summary':'SHARED_PLAN_MARKER'})}]
        captured=[]
        def reply(request,**kwargs):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'content':'VALUE = 1\n'})}}]}).encode())
        with patch('local_model.urllib.request.urlopen',side_effect=reply):
            Llama('fake','http://not-used.invalid').run(task,'developer',history,files,lambda *args:None)
        payload=json.dumps(captured)
        for marker in ('KEEP_MARKER','SHARED_PLAN_MARKER','PREFERENCE_MARKER'):self.assertIn(marker,payload)
        self.assertNotIn('EXCLUDE_MARKER',payload)
        self.assertEqual((files.folder/'value.py').read_text(),'VALUE = 1\n')

    def test_ollama_uses_agent_preferences_and_addressed_history(self):
        task={'title':'plan','agent':{'id':'builder','instructions':'PREFERENCE_MARKER'}}
        history=[{'agent':'user','content':json.dumps({'recipient':'checker','summary':'EXCLUDE_MARKER'})}]
        captured=[]
        def reply(request,**kwargs):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'message':{'content':'{"summary":"planned"}'}}).encode())
        with patch('app.urllib.request.urlopen',side_effect=reply):
            Ollama('fake','http://not-used.invalid').run(task,'planner',history,LocalFiles(Path(self.temp.name)/'workspace'),lambda *args:None)
        self.assertIn('PREFERENCE_MARKER',json.dumps(captured))
        self.assertNotIn('EXCLUDE_MARKER',json.dumps(captured))

    def test_role_message_uses_frozen_assignment_and_role_scope(self):
        save_team(self.store,PRESETS[0]);tid=self.store.add('routing','demo')
        save_team(self.store,PRESETS[1]);self.store.message(tid,'developer','role instruction')
        with self.store.connect() as c:history=[dict(r) for r in c.execute("SELECT * FROM messages WHERE kind='handoff'")]
        self.assertEqual(history[0]['recipient'],'maker')
        self.assertEqual(len(relevant_history(history,'developer','maker')),1)
        self.assertEqual(relevant_history(history,'reviewer','maker'),[])
        with self.assertRaises(ValueError):self.store.message(tid,'planner','inactive stage')

    def test_directed_message_and_model_handoff_commit_atomically(self):
        tid=self.store.add('atomic instruction','demo')
        with self.store.connect() as c:c.executescript("CREATE TRIGGER deny_handoff BEFORE INSERT ON messages WHEN NEW.kind='handoff' BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):self.store.message(tid,'builder','must not partly commit')
        snapshot=self.store.snapshot()
        self.assertEqual(snapshot['deliveries'],[])
        self.assertEqual(snapshot['outbox_pending'],0)
        self.assertFalse(any('must not partly commit' in m['content'] for m in snapshot['messages']))

    def test_overlapping_roles_share_stages_and_separate_review(self):
        team=copy.deepcopy(PRESETS[2])
        team['agents']=[{'id':name,'name':name,'capabilities':['planner','developer','reviewer','reporter'],'instructions':''} for name in ('alpha','beta')]
        save_team(self.store,team)
        assignment=make_plan(self.store,'0000')['assignment']
        self.assertEqual(list(assignment.values()).count('alpha'),2)
        self.assertEqual(list(assignment.values()).count('beta'),2)
        self.assertNotEqual(assignment['developer'],assignment['reviewer'])
        tid=self.store.add('overlap execution','demo');Worker(self.store).tick()
        starts=[m for m in self.store.snapshot()['messages'] if m['task_id']==tid and m['kind']=='start']
        self.assertEqual(len(set(m['agent'] for m in starts)),2)

    def test_one_person_remains_supported(self):
        save_team(self.store,PRESETS[0])
        self.assertEqual(set(make_plan(self.store,'0000')['assignment'].values()),{'maker'})

    def test_unassigned_member_cannot_silently_receive_execution_instruction(self):
        team=copy.deepcopy(PRESETS[1]);team['agents'].append({'id':'reserve','name':'예비 담당','capabilities':['reporter'],'instructions':''})
        save_team(self.store,team);tid=self.store.add('assigned members only','demo')
        with self.assertRaisesRegex(ValueError,'배정되지 않은'):self.store.message(tid,'reserve','cannot execute here')

    def test_legacy_message_migration_preserves_original_record(self):
        folder=Path(self.temp.name)/'legacy';folder.mkdir()
        connection=sqlite3.connect(folder/'state.sqlite3')
        try:
            connection.execute('CREATE TABLE messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,agent TEXT,kind TEXT,content TEXT,created REAL)')
            connection.execute('INSERT INTO messages VALUES(42,?,?,?,?,?)',('old-task','user','handoff','old message',123.5))
            connection.commit()
        finally:connection.close()
        migrated=Store(folder)
        with migrated.connect() as c:row=dict(c.execute('SELECT * FROM messages WHERE seq=42').fetchone())
        self.assertEqual([row[key] for key in ('task_id','agent','kind','content','created')],['old-task','user','handoff','old message',123.5])
        self.assertEqual((row['recipient'],row['capability']),('',''))

    def test_agent_exports_include_incoming_and_legacy_messages(self):
        tid=self.store.add('history','demo');self.store.message(tid,'builder','incoming instruction')
        self.store.log(tid,'user','handoff',json.dumps({'recipient':'checker','summary':'legacy incoming'}))
        worker=Worker(self.store);worker.tick()
        report=json.loads((Path(self.temp.name)/'tasks'/tid/'report.json').read_text(encoding='utf-8'))
        plan=report['team']
        with self.store.connect() as c:messages=task_messages(c,tid,plan,agent='builder')
        self.assertTrue(any('incoming instruction' in m['content'] and m['agent']=='user' for m in messages))
        exported=json.loads((Path(self.temp.name)/'tasks'/tid/'agents/checker/messages.json').read_text(encoding='utf-8'))
        self.assertTrue(any('legacy incoming' in m['content'] for m in exported))
        starts=[m for m in report['history'] if m['kind']=='start']
        self.assertEqual([m['capability'] for m in starts],['planner','developer','reviewer'])
        self.assertTrue(any(m['recipient']=='checker' for m in report['history'] if m['kind']=='handoff'))
