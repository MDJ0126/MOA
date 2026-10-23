from datetime import datetime,timedelta,timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from app import Store,Worker
from setup_config import PRESETS,save_team
from model_transport import ModelError,request_json,retry_delay,completion,model_output,validate_url
from contextlib import nullcontext
from setup_config import write_setting
from vm_runtime import DEFAULT


class ModelTransportTests(unittest.TestCase):
    def setUp(self):
        self.status=400;self.payload=b'{"error":{"message":"bad request"}}';self.headers={};self.calls=0
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.calls+=1;self.rfile.read(int(self.headers.get('Content-Length',0)))
                self.send_response(owner.status)
                for key,value in owner.headers.items():self.send_header(key,value)
                self.send_header('Content-Length',str(len(owner.payload)));self.end_headers();self.wfile.write(owner.payload)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def request(self):return request_json(urllib.request.Request(self.url+'/v1/chat/completions',data=b'{}'),timeout=2)
    def store(self):
        store=Store(self.temp.name);save_team(store,PRESETS[0]);store.add('model failure','llama');return store

    def test_context_error_is_actionable_without_echoing_server_secrets(self):
        self.payload=b'{"error":{"message":"maximum context length exceeded TOKEN_SECRET"}}'
        with self.assertRaises(ModelError) as caught:self.request()
        error=caught.exception;self.assertEqual(error.code,'context_limit');self.assertFalse(error.retryable)
        self.assertEqual(error.status,400);self.assertIn('입력 길이',str(error));self.assertNotIn('TOKEN_SECRET',json.dumps(error.details()))
        self.payload=b'{"error":{"message":"unsupported parameter context length"}}'
        with self.assertRaises(ModelError) as caught:self.request()
        self.assertEqual(caught.exception.code,'invalid_request')

    def test_permanent_request_error_is_not_automatically_retried(self):
        store=self.store();Worker(store,'stub',self.url).tick()
        task=store.snapshot()['tasks'][0]
        self.assertEqual((task['status'],task['failures'],self.calls),('failed',1,1))
        self.assertEqual(task['retry_at'],0)
        self.assertTrue(any(m['kind']=='model_error' for m in store.snapshot()['messages']))

    def test_retry_after_and_three_attempt_bound_are_enforced(self):
        self.status=503;self.headers={'Retry-After':'30'};store=self.store();worker=Worker(store,'stub',self.url)
        before=time.time();worker.tick();task=store.snapshot()['tasks'][0]
        self.assertEqual(task['status'],'queued');self.assertGreaterEqual(task['retry_at'],before+29)
        for _ in range(2):
            with store.connect() as c:c.execute('UPDATE tasks SET retry_at=0')
            worker.tick()
        task=store.snapshot()['tasks'][0]
        self.assertEqual((task['status'],task['failures'],self.calls),('failed',3,3))

    def test_vm_input_step_is_not_automatically_replayed(self):
        store=Store(self.temp.name);save_team(store,PRESETS[0]);write_setting(store,'vm',dict(DEFAULT,enabled=True))
        store.add('no duplicate guest input','llama',environment='vm',vm_actions=[{'role':'developer','action':'text','text':'example'}])
        self.status=503;worker=Worker(store,'stub',self.url)
        with patch.object(worker.vm,'stage',return_value=nullcontext()):worker.tick()
        task=store.snapshot()['tasks'][0]
        self.assertEqual((task['status'],self.calls),('failed',1));self.assertIn('자동 재시도하지',task['error'])
        detail=next(json.loads(m['content']) for m in store.snapshot()['messages'] if m['kind']=='model_error')
        self.assertEqual(detail['automatic_retry_blocked'],'vm_input');self.assertFalse(detail['will_retry'])

    def test_crashed_vm_input_stage_waits_for_manual_confirmation(self):
        store=Store(self.temp.name);save_team(store,PRESETS[0]);write_setting(store,'vm',dict(DEFAULT,enabled=True))
        tid=store.add('crash recovery','demo',environment='vm',vm_actions=[{'role':'developer','action':'text','text':'example'}])
        with store.connect() as c:c.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
        Worker(store).recover();task=store.snapshot()['tasks'][0]
        self.assertEqual((task['status'],task['failures']),('interrupted',0));self.assertIn('자동 복구를 보류',task['error'])
        Worker(store).recover();self.assertEqual(store.snapshot()['tasks'][0]['status'],'interrupted')

    def test_invalid_stored_plan_does_not_enter_recovery_loop(self):
        store=self.store()
        with store.connect() as c:c.execute("UPDATE tasks SET status='running',execution_plan='invalid json'")
        Worker(store).recover();self.assertEqual(store.snapshot()['tasks'][0]['status'],'interrupted')

    def test_cancel_after_error_update_cannot_publish_failed_report(self):
        store=self.store();log=store.log
        def cancel(tid,agent,kind,content,*args,**kwargs):
            result=log(tid,agent,kind,content,*args,**kwargs)
            if kind=='error':store.control('cancel',tid)
            return result
        with patch.object(store,'log',side_effect=cancel):Worker(store,'stub',self.url).tick()
        task=store.snapshot()['tasks'][0]
        self.assertEqual(task['status'],'cancelled')
        self.assertFalse((Path(self.temp.name)/'tasks'/task['id']/'report.json').exists())

    def test_authentication_and_missing_model_are_permanent(self):
        for status,code in ((401,'authentication'),(403,'authentication'),(404,'model_not_found')):
            with self.subTest(status=status):
                self.status=status
                with self.assertRaises(ModelError) as caught:self.request()
                self.assertEqual(caught.exception.code,code);self.assertFalse(caught.exception.retryable)

    def test_html_invalid_utf8_and_missing_completion_are_readable(self):
        self.status=200
        for payload in (b'<html>error</html>',b'\xff',b'[]'):
            self.payload=payload
            with self.assertRaises(ModelError) as caught:self.request()
            self.assertEqual(caught.exception.code,'invalid_response')
        with self.assertRaises(ModelError):completion({'choices':[]})
        for raw in ('not json','[]','null'):
            with self.assertRaises(ModelError) as caught:model_output(raw)
            self.assertEqual(caught.exception.code,'invalid_model_output')

    def test_response_size_limit_precedes_json_parsing(self):
        self.status=200;self.payload=b'X'*33
        with patch('model_transport.MAX_RESPONSE',16):
            with self.assertRaises(ModelError) as caught:self.request()
        self.assertEqual(caught.exception.code,'response_too_large')

    def test_certificate_error_is_not_transient(self):
        error=urllib.error.URLError(ssl.SSLCertVerificationError('test certificate failure'))
        with patch('model_transport.urllib.request.urlopen',side_effect=error):
            with self.assertRaises(ModelError) as caught:self.request()
        self.assertEqual(caught.exception.code,'certificate');self.assertFalse(caught.exception.retryable)

    def test_retry_after_is_bounded_and_accepts_http_dates(self):
        self.assertEqual(retry_delay('99999'),300)
        for value in ('NaN','inf','-1','invalid'):self.assertIsNone(retry_delay(value))
        future=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=60),usegmt=True)
        self.assertTrue(55<=retry_delay(future)<=60)

    def test_invalid_addresses_fail_without_a_network_request(self):
        for url in ('not a URL','ftp://localhost','http://localhost:bad','http://user:SECRET@localhost',None):
            with self.assertRaises(ModelError) as caught:validate_url(url)
            self.assertEqual(caught.exception.code,'invalid_address')
            self.assertNotIn('SECRET',str(caught.exception))
        store=self.store();Worker(store,'stub','http://localhost:bad').tick()
        self.assertEqual(store.snapshot()['tasks'][0]['status'],'failed');self.assertEqual(self.calls,0)
