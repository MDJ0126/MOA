from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
import install_model as installer
from supervisor import acquire


class ModelInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.temp.name);self.path=self.root/'model.gguf'
        self.content=b'fake-model\x00'*150000
        self.sha=hashlib.sha256(self.content).hexdigest()
        self.requests=[]

    def tearDown(self):self.temp.cleanup()

    @contextmanager
    def server(self,mode='range'):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested=self.headers.get('Range');owner.requests.append(requested)
                start=int(requested[6:-1]) if requested else 0
                if mode=='range' and start>=len(owner.content):
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{len(owner.content)}');self.end_headers();return
                partial=bool(requested) and mode!='ignore'
                payload=owner.content[start:] if partial else owner.content
                self.send_response(206 if partial else 200)
                self.send_header('Content-Length',str(len(payload)))
                if partial:self.send_header('Content-Range',f'bytes {start+1 if mode=="wrong" else start}-{len(owner.content)-1}/{len(owner.content)}')
                self.end_headers()
                if mode=='interrupted' and len(owner.requests)==1:
                    self.wfile.write(payload[:1024*1024+100]);self.wfile.flush();self.close_connection=True;return
                self.wfile.write(payload)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield f'http://127.0.0.1:{server.server_port}/model'
        finally:server.shutdown();server.server_close();thread.join(timeout=3)

    def test_resume_uses_verified_offset_and_publishes_hash(self):
        partial=self.path.with_suffix('.gguf.part');partial.write_bytes(self.content[:12345])
        with self.server() as url:installer.download_verified(self.path,url,self.sha)
        self.assertEqual(self.requests,['bytes=12345-'])
        self.assertEqual(installer.digest(self.path),self.sha);self.assertFalse(partial.exists())

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        self.path.with_suffix('.gguf.part').write_bytes(b'old partial')
        with self.server('ignore') as url:installer.download_verified(self.path,url,self.sha)
        self.assertEqual(installer.digest(self.path),self.sha)

    def test_actual_interrupted_response_resumes_on_next_run(self):
        partial=self.path.with_suffix('.gguf.part')
        with self.server('interrupted') as url:
            with self.assertRaises((ValueError,OSError)):installer.download_verified(self.path,url,self.sha)
            self.assertFalse(self.path.exists());offset=partial.stat().st_size
            self.assertGreater(offset,0);self.assertLess(offset,len(self.content))
            installer.download_verified(self.path,url,self.sha)
        self.assertEqual(self.requests,[None,f'bytes={offset}-']);self.assertEqual(installer.digest(self.path),self.sha)

    def test_bad_range_preserves_existing_file_and_partial(self):
        self.path.write_bytes(b'existing');partial=self.path.with_suffix('.gguf.part');partial.write_bytes(b'partial')
        with self.server('wrong') as url:
            with self.assertRaisesRegex(ValueError,'범위'):installer.download_verified(self.path,url,self.sha)
        self.assertEqual(self.path.read_bytes(),b'existing');self.assertEqual(partial.read_bytes(),b'partial')

    def test_hash_mismatch_preserves_existing_file_and_clears_bad_partial(self):
        self.path.write_bytes(b'existing')
        with self.server() as url:
            with self.assertRaisesRegex(RuntimeError,'SHA-256'):installer.download_verified(self.path,url,'0'*64)
        self.assertEqual(self.path.read_bytes(),b'existing');self.assertFalse(self.path.with_suffix('.gguf.part').exists())

    def test_oversized_partial_recovers_from_416(self):
        self.path.with_suffix('.gguf.part').write_bytes(self.content+b'extra')
        with self.server() as url:installer.download_verified(self.path,url,self.sha)
        self.assertEqual(len(self.requests),2);self.assertIsNone(self.requests[1]);self.assertEqual(installer.digest(self.path),self.sha)

    def test_complete_partial_and_installed_file_need_no_network(self):
        self.path.with_suffix('.gguf.part').write_bytes(self.content)
        with patch('install_model.urllib.request.urlopen',side_effect=AssertionError('network')):
            installer.download_verified(self.path,'unused',self.sha)
            installer.download_verified(self.path,'unused',self.sha)

    def test_duplicate_installer_is_rejected(self):
        (self.root/'runtime').mkdir()
        with acquire(self.root/'runtime'/'model-install.lock'),patch.object(installer,'ROOT',self.root),patch.object(installer,'install') as install:
            with self.assertRaisesRegex(RuntimeError,'다른 모델 설치'):installer.main()
            install.assert_not_called()

    def test_extraction_keeps_matching_file_and_failed_replacement(self):
        archive=self.root/'runtime.zip';dest=self.root/'llama';dest.mkdir();target=dest/'server.exe';target.write_bytes(b'old')
        with zipfile.ZipFile(archive,'w') as z:z.writestr('server.exe',b'new')
        replace=Path.replace
        def fail(staged,other):
            if other==target:raise PermissionError('loaded file')
            return replace(staged,other)
        with patch.object(Path,'replace',fail):
            with self.assertRaises(PermissionError):installer.extract_runtime(archive,dest)
        self.assertEqual(target.read_bytes(),b'old')
        installer.extract_runtime(archive,dest)
        with patch.object(Path,'replace',side_effect=AssertionError('matching file replaced')),patch.object(zipfile.ZipFile,'read',side_effect=AssertionError('whole member read')):installer.extract_runtime(archive,dest)
        self.assertEqual(target.read_bytes(),b'new')

    def test_hardlink_download_does_not_modify_original(self):
        private=self.root/'private';private.write_bytes(b'private');os.link(private,self.path)
        with self.assertRaises(ValueError):installer.download_verified(self.path,'unused',self.sha)
        self.assertEqual(private.read_bytes(),b'private')

    def test_corrupt_member_and_path_escape_preserve_existing_runtime(self):
        archive=self.root/'runtime.zip';dest=self.root/'llama';dest.mkdir();target=dest/'server.exe';target.write_bytes(b'existing')
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as z:z.writestr('server.exe',b'new-runtime-marker')
        archive.write_bytes(archive.read_bytes().replace(b'new-runtime-marker',b'bad-runtime-marker'))
        with self.assertRaises(zipfile.BadZipFile):installer.extract_runtime(archive,dest)
        self.assertEqual(target.read_bytes(),b'existing')
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('server.exe',b'new');z.writestr('../escaped',b'bad')
        with self.assertRaisesRegex(ValueError,'archive path'):installer.extract_runtime(archive,dest)
        self.assertEqual(target.read_bytes(),b'existing');self.assertFalse((self.root/'escaped').exists())

    def test_failed_install_preserves_previous_manifest(self):
        (self.root/'models').mkdir();manifest=self.root/'models'/'installed.json';manifest.write_text('previous-install')
        with patch.object(installer,'ROOT',self.root),patch.object(installer,'download_verified',side_effect=OSError('disconnected')):
            with self.assertRaises(OSError):installer.main()
        self.assertEqual(manifest.read_text(),'previous-install')

    def test_real_small_install_writes_manifest_only_after_verification(self):
        archive=self.root/'fixture.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('llama-server.exe',b'fake-runtime')
        runtime=archive.read_bytes()
        class Response:
            status=200
            def __init__(self,content):
                import io
                self.stream=io.BytesIO(content);self.headers={'Content-Length':str(len(content))}
            def read(self,size):return self.stream.read(size)
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
        artifacts=[('runtime/model.zip','https://test/runtime',hashlib.sha256(runtime).hexdigest()),('models/model.gguf','https://test/model',self.sha)]
        def open_fixture(request,**kwargs):return Response(runtime if request.full_url.endswith('runtime') else self.content)
        with patch.object(installer,'ROOT',self.root),patch.object(installer,'ARTIFACTS',artifacts),patch('install_model.urllib.request.urlopen',side_effect=open_fixture):installer.main()
        self.assertEqual((self.root/'runtime'/'llama'/'llama-server.exe').read_bytes(),b'fake-runtime')
        self.assertEqual(len(json.loads((self.root/'models'/'installed.json').read_text())),2)
