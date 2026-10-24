from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from app import Store
from app_lifecycle import request_stop
from setup_config import PRESETS,save_team
from verify_clean_install import copy_sources

class HistoryRecoveryTests(unittest.TestCase):
    @contextmanager
    def server(self,raw='{broken'):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as temporary:
            root=Path(temporary);project=root/'source';copy_sources(project);store=Store(root/'data');save_team(store,PRESETS[1])
            tid=store.add('ORIGINAL_REQUEST_MARKER','demo');store.message(tid,'builder','ADDRESSED_MARKER',request_key='history-recovery-message');store.control('pause')
            with store.connect() as c:c.execute("UPDATE tasks SET execution_plan=?,status='interrupted' WHERE id=?",(raw,tid))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            with (root/'server.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,str(project/'src/app.py'),'--data',str(store.root),'--port',str(port),'--model','','--model-url','http://127.0.0.1:0'],stdout=log,stderr=log)
                try:
                    base='http://127.0.0.1:'+str(port)
                    for _ in range(100):
                        try:urllib.request.urlopen(base+'/api/health',timeout=1).close();break
                        except OSError:
                            if process.poll() is not None:self.fail('isolated app exited')
                            time.sleep(.05)
                    else:self.fail('isolated app readiness timeout')
                    yield store,tid,base
                finally:
                    if process.poll() is None:
                        try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=10)
                        except Exception:process.terminate();process.wait(timeout=10)
    def test_binary_and_structural_damage_keeps_state_and_history_readable(self):
        values=(b'\xff\x00','[]','{"agents":null}','0')
        with self.server(values[0]) as (store,tid,base):
            for raw in values:
                with self.subTest(raw=raw):
                    with store.connect() as c:c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',(raw,tid))
                    with urllib.request.urlopen(base+'/api/state',timeout=5) as response:state=json.load(response)
                    task=next(t for t in state['tasks'] if t['id']==tid)
                    self.assertTrue(task['execution_plan_unavailable'])
                    with urllib.request.urlopen(base+'/api/logs?task='+tid+'&agent=builder',timeout=5) as response:result=json.load(response)
                    self.assertTrue(result['plan_unavailable']);self.assertIn('ADDRESSED_MARKER',str(result['messages']))
                    with store.connect() as c:self.assertEqual(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0],raw)

    def test_corrupt_plan_keeps_original_history_readable_and_exportable(self):
        with self.server() as (store,tid,base):
            with urllib.request.urlopen(base+'/api/logs?task='+tid,timeout=5) as response:result=json.load(response)
            self.assertTrue(result['plan_unavailable'])
            self.assertIn('ORIGINAL_REQUEST_MARKER',str(result['messages']));self.assertIn('ADDRESSED_MARKER',str(result['messages']))
            with urllib.request.urlopen(base+'/api/logs/export?task='+tid+'&agent=builder',timeout=5) as response:export=json.load(response)
            self.assertTrue(export['plan_unavailable']);self.assertIn('ADDRESSED_MARKER',str(export['messages']))
            with store.connect() as c:self.assertEqual(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0],'{broken')

    def test_null_legacy_message_keeps_history_and_exports_readable(self):
        with self.server() as (store,tid,base):
            with store.connect() as c:
                c.execute('INSERT INTO messages(task_id,agent,kind,content,created) VALUES(?,?,?,?,?)',(tid,'builder','message',None,time.time()))
                seq=c.execute('SELECT max(seq) FROM messages').fetchone()[0]
            for route in ('/api/logs?task=','/api/logs/export?task='):
                for suffix in ('','&agent=builder'):
                    with self.subTest(route=route,suffix=suffix),urllib.request.urlopen(base+route+tid+suffix,timeout=5) as response:
                        result=json.load(response)
                    row=next(message for message in result['messages'] if message['seq']==seq)
                    self.assertIsNone(row['content']);self.assertTrue(result['plan_unavailable'])
            with store.connect() as c:self.assertIsNone(c.execute('SELECT content FROM messages WHERE seq=?',(seq,)).fetchone()[0])

    def test_structured_invalid_recipients_are_not_broadcast(self):
        from setup_config import legacy_plan
        for raw in (json.dumps(legacy_plan()),'{broken'):
            with self.subTest(plan=raw),self.server(raw) as (store,tid,base):
                records=[json.dumps({'recipient':value,'content':'DAMAGED_RECIPIENT'}) for value in ([],{},None,17)]
                with store.connect() as c:
                    c.executemany('INSERT INTO messages(task_id,agent,kind,content,created) VALUES(?,?,?,?,?)',[(tid,'user','instruction',content,time.time()) for content in records])
                for route in ('/api/logs?task=','/api/logs/export?task='):
                    with urllib.request.urlopen(base+route+tid,timeout=5) as response:all_messages=json.load(response)['messages']
                    expected=list(reversed(records)) if route.startswith('/api/logs?') else records
                    self.assertEqual([m['content'] for m in all_messages if 'DAMAGED_RECIPIENT' in (m['content'] or '')],expected)
                    with urllib.request.urlopen(base+route+tid+'&agent=developer',timeout=5) as response:filtered=json.load(response)['messages']
                    self.assertFalse(any('DAMAGED_RECIPIENT' in (m['content'] or '') for m in filtered))
                with store.connect() as c:
                    self.assertEqual([r[0] for r in c.execute("SELECT content FROM messages WHERE task_id=? AND kind='instruction' AND content LIKE '%DAMAGED_RECIPIENT%' ORDER BY seq",(tid,))],records)

    def test_nonfinite_plan_metadata_does_not_leak_nonstandard_json(self):
        from setup_config import legacy_plan
        plan=legacy_plan();plan['legacy_metadata']={'limit':'NUMERIC_MARKER'}
        values=[(number,json.dumps(plan).replace('"NUMERIC_MARKER"',number)) for number in ('NaN','Infinity','-Infinity','1e309')]
        with self.server(values[0][1]) as (store,tid,base):
            for number,raw in values:
                with self.subTest(number=number):
                    with store.connect() as c:c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',(raw,tid))
                    for route in ('/api/logs/export?task='+tid,'/api/logs?task='+tid,'/api/state'):
                        with urllib.request.urlopen(base+route,timeout=5) as response:
                            value=json.loads(response.read(),parse_constant=lambda token:self.fail('nonstandard JSON constant: '+token))
                        if route=='/api/state':self.assertTrue(next(task for task in value['tasks'] if task['id']==tid)['execution_plan_unavailable'])
                        else:
                            self.assertTrue(value['plan_unavailable']);self.assertIn('ORIGINAL_REQUEST_MARKER',str(value['messages']))
                    with store.connect() as c:self.assertEqual(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0],raw)

if __name__=='__main__':unittest.main()
