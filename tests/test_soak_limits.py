import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from soak import run, checked_tree, prune_verified


class SoakLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent)
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def simulate(self,paused=False,stop_error=None,**options):
        clock=[0.0];child=MagicMock();child.process.pid=123;child.process.poll.return_value=None;child.status.return_value={}
        requests=[]
        def freeze(destination):
            destination.mkdir();(destination/'src').mkdir();(destination/'src/app.py').write_text('test',encoding='utf-8');return {'app.py':'frozen'}
        def response(request,**kwargs):
            requests.append(request.full_url)
            if request.full_url.endswith('/api/health'):
                data={'app':{'pid':123},'app_source_sha256':'frozen','worker':{'thread_alive':True},'queue':{'paused':paused}}
            elif request.data:data={'id':'0123456789ab'}
            else:data={'tasks':[{'id':'0123456789ab','status':'queued'}]}
            stream=io.BytesIO(json.dumps(data).encode());stream.headers={'Content-Type':'application/json'};return stream
        def sleep(seconds):clock[0]+=seconds
        with patch('soak.freeze',side_effect=freeze),patch('soak.ManagedChild',return_value=child),patch('soak.stop_owned_app',side_effect=stop_error) as stop,patch('soak.urllib.request.urlopen',side_effect=response),patch('soak.time.monotonic',side_effect=lambda:clock[0]),patch('soak.time.sleep',side_effect=sleep):
            result=run(self.root/'run',seconds=600,interval=.2,job_interval=1,**options)
        if options.get('max_bytes')==1:child.step.assert_not_called()
        stop.assert_called_once();return result,requests

    def test_paused_queue_fails_before_submission(self):
        result,requests=self.simulate(paused=True)
        self.assertEqual(result['status'],'failed');self.assertIn('queue paused',result['error'])
        self.assertFalse(any(url.endswith('/api/tasks') for url in requests));self.assertLess(result['elapsed_seconds'],1)

    def test_pending_task_has_independent_timeout(self):
        result,_=self.simulate(task_timeout=2)
        self.assertEqual(result['status'],'failed');self.assertIn('task timeout',result['error'])
        self.assertLess(result['elapsed_seconds'],3);self.assertEqual(result['pending_task'],'0123456789ab')
        self.assertFalse(result['demo_24h_observed'])

    def test_shutdown_failure_still_records_terminal_state_and_original_error(self):
        result,_=self.simulate(paused=True,stop_error=OSError('close failed'))
        self.assertEqual(result['status'],'failed');self.assertIn('queue paused',result['error'])
        self.assertIn('close failed',result['cleanup_error']);self.assertFalse(result['demo_24h_observed'])
        saved=json.loads((self.root/'run/status.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['status'],'failed');self.assertEqual(saved['cleanup_error'],result['cleanup_error'])

    def test_byte_budget_fails_and_preserves_evidence(self):
        result,requests=self.simulate(max_bytes=1)
        self.assertEqual(result['status'],'failed');self.assertIn('byte budget',result['error'])
        self.assertGreater(result['observed_bytes'],1);self.assertFalse(requests)
        self.assertTrue((self.root/'run/source/src/app.py').is_file())

    def test_only_named_verified_workspace_is_pruned(self):
        tasks=self.root/'data/tasks';target=tasks/'0123456789ab';target.mkdir(parents=True);(target/'report').write_bytes(b'abc')
        other=tasks/'abcdef012345';other.mkdir();(other/'report').write_bytes(b'defg')
        database=self.root/'data/state.db';database.write_bytes(b'preserved')
        self.assertEqual(checked_tree(target),3)
        prune_verified(self.root,'0123456789ab')
        self.assertFalse(target.exists());self.assertTrue(other.exists());self.assertEqual(database.read_bytes(),b'preserved')
        for tid in ('../data','',None,'ABCDEF012345'):
            with self.assertRaises(ValueError):prune_verified(self.root,tid)

    def test_linked_entry_is_preserved(self):
        target=self.root/'data/tasks/0123456789ab';target.mkdir(parents=True)
        source=self.root/'outside';source.write_bytes(b'preserved')
        (target/'linked').hardlink_to(source)
        with self.assertRaises(ValueError):prune_verified(self.root,'0123456789ab')
        self.assertEqual(source.read_bytes(),b'preserved');self.assertTrue((target/'linked').exists())

    def test_invalid_limits_do_not_create_run(self):
        for options in ({'task_timeout':0},{'keep_artifacts':-1},{'keep_artifacts':True},{'max_bytes':0}):
            with self.assertRaises(ValueError):run(self.root/'absent',**options)
            self.assertFalse((self.root/'absent').exists())

    def test_cleanup_failure_is_not_counted_as_http_failure(self):
        target=self.root/'data/tasks/0123456789ab';target.mkdir(parents=True)
        with patch('soak.shutil.rmtree',side_effect=PermissionError('locked')):
            with self.assertRaisesRegex(RuntimeError,'cleanup failed'):prune_verified(self.root,'0123456789ab')
        self.assertTrue(target.exists())

    def test_quota_sample_tolerates_replaced_temp_file_but_prune_is_strict(self):
        transient=self.root/'status.json.tmp';transient.write_bytes(b'temporary')
        original=Path.lstat
        def disappear(path):
            if path==transient:raise FileNotFoundError('atomic status replacement')
            return original(path)
        with patch.object(Path,'lstat',disappear):
            self.assertEqual(checked_tree(self.root,ignore_missing=True),0)
            with self.assertRaises(FileNotFoundError):checked_tree(self.root)
