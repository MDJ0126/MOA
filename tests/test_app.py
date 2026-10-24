import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from app import Store, Worker, LocalFiles, Ollama, Demo
from checks import run_checks
from integrations import flush_outbox


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent)
        self.store = Store(self.temp.name)
        self.worker = Worker(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_fifo_handoff_artifact_and_persistence(self):
        a = self.store.add('한글 작업 A', 'demo')
        b = self.store.add('작업 B', 'demo')
        self.worker.tick()
        state = Store(self.temp.name).snapshot()
        by_id = {t['id']: t for t in state['tasks']}
        self.assertEqual(by_id[a]['status'], 'completed')
        self.assertEqual(by_id[b]['status'], 'queued')
        handoffs = [m['agent'] for m in state['messages'] if m['kind']=='handoff']
        self.assertEqual(handoffs, ['planner','developer','reviewer','reporter'])
        report = json.loads((Path(self.temp.name)/'tasks'/a/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['mode'], 'demo')
        self.assertEqual(len(report['files'][0]['sha256']), 64)
        self.worker.tick()
        self.assertTrue(all(t['status']=='completed' for t in self.store.snapshot()['tasks']))

    def test_pause_cancel(self):
        tid = self.store.add('pause', 'demo')
        self.store.control('pause')
        self.assertFalse(self.worker.tick())
        self.store.control('cancel',tid)
        self.store.control('resume')
        self.assertFalse(self.worker.tick())
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'cancelled')

    def test_recovery_does_not_silently_replay(self):
        tid = self.store.add('recover','demo',auto_recover=False)
        with self.store.connect() as c:
            c.execute("UPDATE tasks SET status='running',stage=2 WHERE id=?",(tid,))
        self.worker.recover()
        self.assertFalse(self.worker.tick())
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'interrupted')
        files = LocalFiles(Path(self.temp.name)/'tasks'/tid/'workspace')
        files.execute({'tool':'write','path':'demo-result.md','content':'# 데모 결과물\n\nrecover\n'},True)
        self.store.control('retry',tid)
        self.worker.tick()
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'completed')
        starts=[m['agent'] for m in self.store.snapshot()['messages'] if m['kind']=='start']
        self.assertEqual(starts,['reviewer','reporter'])

    def test_failed_verification_never_completed(self):
        self.store.add('broken','demo',max_revisions=0)
        original = Demo.run
        def fail(backend, task, role, history, files, log):
            if role == 'reviewer':
                return {'summary':'실패','passed':False}
            return original(backend,task,role,history,files,log)
        with patch.object(Demo,'run',fail):
            self.worker.tick()
        t = self.store.snapshot()['tasks'][0]
        self.assertEqual((t['status'],t['stage']),('failed',2))
        self.assertEqual(json.loads((Path(self.temp.name)/'tasks'/t['id']/'report.json').read_text(encoding='utf-8'))['status'],'failed')

    def test_path_escape_and_reviewer_write_denied(self):
        files=LocalFiles(Path(self.temp.name)/'workspace')
        for p in ('../escape.txt', str(Path(self.temp.name)/'escape.txt')):
            with self.assertRaises(ValueError):
                files.execute({'tool':'write','path':p,'content':'x'}, True)
        with self.assertRaises(ValueError):
            files.execute({'tool':'write','path':'a.txt','content':'x'}, False)

    def test_missing_real_model_fails_honestly(self):
        self.store.add('real','ollama')
        self.worker.tick()
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'failed')

    def test_failed_review_routes_back_to_developer(self):
        tid=self.store.add('repair','demo')
        original=Demo.run
        reviews=[]
        def review(backend, task, role, history, files, log):
            if role=='reviewer' and not reviews:
                reviews.append(1);return {'summary':'수정 필요','passed':False}
            return original(backend,task,role,history,files,log)
        with patch.object(Demo,'run',review):
            self.worker.tick()
            t=self.store.snapshot()['tasks'][0]
            self.assertEqual((t['status'],t['stage'],t['revision']),('queued',1,1))
            self.worker.tick()
        state=self.store.snapshot()
        self.assertEqual(state['tasks'][0]['status'],'completed')
        self.assertTrue(any(d['sender']=='reviewer' and d['recipient']=='developer' for d in state['deliveries']))
        self.assertTrue((Path(self.temp.name)/'tasks'/tid/'agents'/'developer'/'messages.json').exists())
        self.assertTrue((Path(self.temp.name)/'tasks'/tid/'artifacts.zip').exists())

    def test_fixed_acceptance_cannot_hide_failed_workspace_tests(self):
        files=LocalFiles(Path(self.temp.name)/'workspace')
        files.execute({'tool':'write','path':'logic.py','content':'def value(): return 1\n'},True)
        files.execute({'tool':'write','path':'test_logic.py','content':'import unittest\nclass Bad(unittest.TestCase):\n def test_bad(self): self.assertEqual(1,2)\n'},True)
        acceptance='import unittest\nfrom logic import value\nclass Good(unittest.TestCase):\n def test_good(self): self.assertEqual(value(),1)\n'
        result=run_checks(files.folder,acceptance)
        self.assertFalse(result['passed']);self.assertEqual(result['tests_run'],2)

    def test_missing_required_artifact_overrides_model_approval(self):
        self.store.add('missing','demo',required_files=['not-created.txt'],max_revisions=0)
        self.worker.tick()
        t=self.store.snapshot()['tasks'][0]
        self.assertEqual(t['status'],'failed')
        self.assertEqual(json.loads(t['verification'])['missing_files'],['not-created.txt'])

    def test_changed_source_invalidates_review(self):
        tid=self.store.add('hash change','demo')
        original=Demo.run
        def mutate(backend,task,role,history,files,log):
            result=original(backend,task,role,history,files,log)
            if role=='reviewer':files.execute({'tool':'write','path':'changed.txt','content':'after review'},True)
            return result
        with patch.object(Demo,'run',mutate):self.worker.tick()
        t=self.store.snapshot()['tasks'][0]
        self.assertEqual((t['status'],t['stage']),('queued',2))
        self.assertFalse((Path(self.temp.name)/'tasks'/tid/'report.json').exists())

    def test_auto_recovery_is_bounded(self):
        self.store.add('auto','demo',auto_recover=True)
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='running',stage=1,failures=2")
        self.worker.recover()
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'queued')
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='running'")
        self.worker.recover()
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'interrupted')

    def test_delivery_outbox_retries_only_notification(self):
        tid=self.store.add('notify','demo')
        self.store.deliver(tid,'reporter','user',{'summary':'report ready'})
        class Adapter:
            def __init__(self):self.calls=0
            def send(self,event_id,payload):
                self.calls+=1
                if self.calls==1:raise ConnectionError('offline')
                return 'receipt'
        adapter=Adapter();flush_outbox(self.store,adapter)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT attempts FROM outbox').fetchone()[0],1)
            c.execute('UPDATE outbox SET available_at=0')
        flush_outbox(self.store,adapter);flush_outbox(self.store,adapter)
        self.assertEqual(adapter.calls,2)
        self.assertEqual(self.store.snapshot()['tasks'][0]['stage'],0)
        self.assertEqual(self.store.snapshot()['outbox_pending'],0)

    def test_ollama_protocol_with_local_stub(self):
        # Protocol integration only: this is not a real model quality test.
        replies = [json.dumps({'tool':'read','path':'evidence.txt'}),json.dumps({'summary':'파일 확인','passed':True})]
        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.requests.append(request)
                body=json.dumps({'message':{'content':replies.pop(0)}}).encode()
                self.send_response(200);self.end_headers();self.wfile.write(body)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Stub)
        server.requests=[]
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            files=LocalFiles(Path(self.temp.name)/'workspace')
            files.execute({'tool':'write','path':'evidence.txt','content':'observed'},True)
            result=Ollama('stub',f'http://127.0.0.1:{server.server_port}').run({'title':'검토'},'reviewer',[],files,lambda *args:None)
            self.assertTrue(result['passed'])
            self.assertIn('observed',server.requests[-1]['messages'][-1]['content'])
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
