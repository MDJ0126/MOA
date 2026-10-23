import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from app import Store,Worker
from setup_config import PRESETS,save_team
from supervisor import ManagedChild
from soak import run
from health import status as observed_status
import threading


class OperationTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_exited_child_releases_log_handles_before_restart(self):
        clock=[0];child=ManagedChild('test',[sys.executable,'-c','print("done");raise SystemExit(3)'],self.root,self.root,clock=lambda:clock[0])
        try:
            for _ in range(3):
                child.step();handles=list(child.handles);child.process.wait(timeout=5);child.step()
                self.assertTrue(all(h.closed for h in handles));self.assertEqual(child.status()['open_log_handles'],0)
                clock[0]=child.next_start
            self.assertEqual(child.restarts,3)
            self.assertEqual(child.status()['last_exit_code'],3)
        finally:child.close()

    def test_failed_launch_closes_handles_and_preserves_supervisor(self):
        child=ManagedChild('test',['missing'],self.root,self.root)
        with patch('supervisor.subprocess.Popen',side_effect=OSError('test launch failure')):child.step()
        self.assertEqual(child.launch_failures,1);self.assertEqual(child.handles,[])
        self.assertGreater(child.status()['retry_in_seconds'],0)

    def test_backoff_resets_after_a_stable_period(self):
        clock=[0];child=ManagedChild('test',['unused'],self.root,self.root,clock=lambda:clock[0])
        class Process:
            pid=123;code=None
            def poll(self):return self.code
        process=Process()
        with patch('supervisor.subprocess.Popen',return_value=process):child.step()
        child.consecutive=5;clock[0]=120;child.step();process.code=1;child.step()
        self.assertEqual(child.next_start,122);child.close()

    def test_heartbeat_write_failure_does_not_stop_demo_work(self):
        store=Store(self.root/'data');save_team(store,PRESETS[0]);store.add('heartbeat failure','demo')
        worker=Worker(store);tick=worker.tick
        def once():
            result=tick();worker.stop.set();return result
        with patch('app.write_status',return_value=False),patch.object(worker,'tick',side_effect=once):worker.loop()
        self.assertEqual(store.snapshot()['tasks'][0]['status'],'completed');self.assertFalse(worker.heartbeat_write_ok)

    def test_reports_and_checkpoints_do_not_use_whole_file_reads(self):
        store=Store(self.root/'data');save_team(store,PRESETS[0]);store.add('bounded reads','demo')
        with patch.object(Path,'read_bytes',side_effect=AssertionError('whole-file read')):Worker(store).tick()
        self.assertEqual(store.snapshot()['tasks'][0]['status'],'completed')

    def test_stale_supervisor_record_is_not_a_liveness_claim(self):
        store=Store(self.root/'data');worker=Worker(store)
        (store.root/'supervisor.json').write_text(json.dumps({'pid':123,'updated':time.time()-300,'restarts':{'app':2}}),encoding='utf-8')
        result=observed_status(store,worker,threading.current_thread(),time.time(),'test-instance')
        self.assertGreaterEqual(result['supervisor_record']['record_age_seconds'],299)
        self.assertNotIn('running',result['supervisor_record'])
        self.assertNotIn('title',json.dumps(result))

    def test_short_frozen_soak_is_not_a_24_hour_claim(self):
        result=run(self.root/'soak',seconds=8,interval=.2,job_interval=.5)
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertGreater(result['verified_tasks'],0);self.assertFalse(result['demo_24h_observed'])
        self.assertFalse(result['actual_model_tested'])
        config=json.loads((self.root/'soak/source/config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['model_url'],'http://127.0.0.1:0');self.assertEqual(config['default_mode'],'demo')
