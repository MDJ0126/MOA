from contextlib import ExitStack
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from app import Store
from setup_config import PRESETS,save_team


class HistoryHTTPTests(unittest.TestCase):
    def test_storage_failure_returns_safe_error_and_replay_is_one_instruction(self):
        root=Path(__file__).parent.parent
        with tempfile.TemporaryDirectory(dir=root) as temporary,ExitStack() as stack:
            data=Path(temporary)/'data';store=Store(data);save_team(store,PRESETS[1]);store.control('pause')
            with store.connect() as c:c.execute("CREATE TRIGGER reject_log BEFORE INSERT ON messages WHEN NEW.kind='instruction' BEGIN SELECT RAISE(ABORT,'private-database-detail'); END")
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            log=stack.enter_context((Path(temporary)/'server.log').open('wb'))
            process=subprocess.Popen([sys.executable,str(root/'app.py'),'--data',str(data),'--port',str(port),'--model','','--model-url','http://127.0.0.1:0'],stdout=log,stderr=log)
            base=f'http://127.0.0.1:{port}'
            def post(path,value):
                request=urllib.request.Request(base+path,json.dumps(value).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
                with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base+'/api/health',timeout=1):break
                    except OSError:
                        if process.poll() is not None:self.fail('isolated app exited')
                        time.sleep(.05)
                else:self.fail('isolated app readiness timeout')
                for name in ('home','team','work','task','slack','environment','actions','diagnostics','backup','operations'):
                    with urllib.request.urlopen(base+'/guide-images/'+name+'.png',timeout=5) as response:
                        self.assertEqual(response.headers.get_content_type(),'image/png')
                        self.assertEqual(response.read(8),b'\x89PNG\r\n\x1a\n')
                for path in ('/guide-images/manifest.json','/guide-images/../config.json','/guide-images/%2e%2e/config.json'):
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+path,timeout=5)
                    self.assertEqual(caught.exception.code,404);caught.exception.close()
                payload={'title':'HTTP atomic','mode':'demo','request_key':'http-task-123'}
                with self.assertRaises(urllib.error.HTTPError) as caught:post('/api/tasks',payload)
                error=caught.exception
                try:
                    self.assertEqual(error.code,503);message=json.load(error)['error'];self.assertNotIn('private-database-detail',message)
                finally:error.close()
                self.assertEqual(store.snapshot()['tasks'],[])
                with store.connect() as c:c.execute('DROP TRIGGER reject_log')
                tid=post('/api/tasks',payload)['id'];self.assertEqual(post('/api/tasks',payload)['id'],tid)
                note={'id':tid,'recipient':'builder','content':'HTTP replay','request_key':'http-message-123'}
                with store.connect() as c:c.execute("CREATE TRIGGER reject_message BEFORE INSERT ON messages WHEN NEW.kind='handoff' BEGIN SELECT RAISE(ABORT,'private-message-detail'); END")
                with self.assertRaises(urllib.error.HTTPError) as caught:post('/api/messages',note)
                error=caught.exception
                try:self.assertEqual(error.code,503);self.assertNotIn('private-message-detail',error.read().decode())
                finally:error.close()
                self.assertEqual(store.snapshot()['deliveries'],[])
                with store.connect() as c:c.execute('DROP TRIGGER reject_message')
                self.assertTrue(post('/api/messages',note)['ok']);self.assertTrue(post('/api/messages',note)['ok'])
                state=store.snapshot();self.assertEqual(len(state['deliveries']),1);self.assertEqual(state['outbox_pending'],1)
                self.assertEqual(len([m for m in state['messages'] if m['kind']=='instruction']),1)
                with store.connect() as c:c.execute("UPDATE outbox SET state='held_restored'")
                with urllib.request.urlopen(base+'/api/slack/recovery',timeout=5) as response:held=json.load(response)
                self.assertEqual(held['counts']['notification'],1)
                selection=[{'kind':held['items'][0]['kind'],'id':held['items'][0]['id']}]
                self.assertEqual(post('/api/slack/recovery',{'action':'dismiss','items':selection})['processed'],1)
                with self.assertRaises(urllib.error.HTTPError) as caught:post('/api/slack/recovery',{'action':'resume','items':selection})
                caught.exception.close()
                with store.connect() as c:
                    self.assertEqual(c.execute('SELECT state FROM outbox').fetchone()[0],'dismissed')
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM slack_recovery_audit').fetchone()[0],1)
                post('/api/control',{'action':'shutdown'});process.wait(timeout=10)
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=10)
