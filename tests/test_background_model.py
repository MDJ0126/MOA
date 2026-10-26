import json,threading,time,unittest,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from background_model import BackgroundYield,request_background_json
from model_transport import ModelError
class BackgroundModelTests(unittest.TestCase):
 def test_foreground_yield_disconnects_a_blocked_http_request(self):
  started=threading.Event();disconnected=threading.Event();cancel=threading.Event()
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    self.rfile.read(int(self.headers.get('Content-Length',0)));started.set();self.connection.settimeout(3)
    if self.connection.recv(1)==b'':disconnected.set()
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  def trigger():
   started.wait(2);cancel.set()
  threading.Thread(target=trigger,daemon=True).start()
  req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/v1/chat/completions',b'{}',{'Content-Type':'application/json'})
  begin=time.monotonic()
  try:
   with self.assertRaises(BackgroundYield):request_background_json(req,4,cancel.is_set)
   elapsed=time.monotonic()-begin;self.assertLess(elapsed,1.5);self.assertTrue(disconnected.wait(1))
  finally:server.shutdown();server.server_close()
 def test_remote_transport_and_error_classification_are_preserved(self):
  req=urllib.request.Request('https://example.invalid/v1/chat/completions',b'{}')
  with patch('background_model.request_json',return_value={'ok':True}) as transport:
   self.assertEqual(request_background_json(req,3,lambda:False),{'ok':True});transport.assert_called_once_with(req,3)

 def test_local_success_and_retryable_error_are_preserved(self):
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    self.rfile.read(int(self.headers.get('Content-Length',0)))
    data=json.dumps({'ok':True}).encode();self.send_response(200 if self.path=='/ok' else 503)
    self.send_header('Content-Length',str(len(data)));self.send_header('Retry-After','2');self.end_headers();self.wfile.write(data)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   prefix='http://127.0.0.1:'+str(server.server_port)
   self.assertEqual(request_background_json(urllib.request.Request(prefix+'/ok',b'{}'),3,lambda:False),{'ok':True})
   with self.assertRaises(ModelError) as error:request_background_json(urllib.request.Request(prefix+'/error',b'{}'),3,lambda:False)
   self.assertTrue(error.exception.retryable);self.assertEqual(error.exception.status,503);self.assertEqual(error.exception.retry_after,2)
  finally:server.shutdown();server.server_close()
