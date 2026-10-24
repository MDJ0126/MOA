import os
import json
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch
from checks import run_checks
from vm_runtime import pid_alive
from app import Store,Worker
from setup_config import PRESETS,save_team


class CheckLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent)
        self.root=Path(self.temp.name);self.workspace=self.root/'workspace';self.workspace.mkdir()
    def tearDown(self):self.temp.cleanup()
    def source(self,code): (self.workspace/'test_limits.py').write_text(code,encoding='utf-8')

    def test_large_python_and_native_outputs_are_bounded(self):
        self.source("import unittest,os\nclass Loud(unittest.TestCase):\n def test_loud(self):\n  for _ in range(4000): print('X'*1000)\n  for _ in range(500):\n   os.write(1,b'N'*8192);os.write(2,b'E'*8192)\n")
        result=run_checks(self.workspace,timeout=10)
        self.assertTrue(result['passed'],result);self.assertEqual(result['tests_run'],1)
        self.assertLessEqual(len(result['output']),20000);self.assertTrue(result['output_truncated'])
        self.assertLessEqual(len(result['native_output']),20000);self.assertTrue(result['native_output_truncated'])

    def descendant(self,hang):
        self.source("import pathlib,subprocess,sys,time,unittest\nchild=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\npathlib.Path('child.pid').write_text(str(child.pid))\nclass Child(unittest.TestCase):\n def test_child(self): "+('time.sleep(30)' if hang else 'self.assertTrue(True)')+'\n')
        started=time.monotonic()
        try:
            result=run_checks(self.workspace,timeout=2 if hang else 10)
            self.assertLess(time.monotonic()-started,9)
            marker=self.workspace/'child.pid';self.assertTrue(marker.exists(),result)
            pid=int(marker.read_text())
            deadline=time.monotonic()+3
            while pid_alive(pid) and time.monotonic()<deadline:time.sleep(.05)
            self.assertFalse(pid_alive(pid),'owned descendant survived verification')
            if hang:self.assertFalse(result['passed']);self.assertTrue(result['timed_out'])
            else:self.assertTrue(result['passed'],result)
        finally:
            marker=self.workspace/'child.pid'
            if marker.exists():
                pid=int(marker.read_text())
                if pid_alive(pid):os.kill(pid,signal.SIGTERM)

    def test_timeout_terminates_own_descendant(self):self.descendant(True)
    def test_success_also_terminates_background_descendant(self):self.descendant(False)

    def test_assignment_failure_never_executes_workspace_code(self):
        self.source("from pathlib import Path\nPath('executed').write_text('bad')\n")
        with patch('check_process.ProcessTree.assign',side_effect=OSError('job unavailable')):result=run_checks(self.workspace)
        self.assertFalse(result['passed']);self.assertIn('job unavailable',result['output'])
        self.assertFalse((self.workspace/'executed').exists())

    def test_timeout_then_next_verification_can_succeed(self):
        self.source('import time\ntime.sleep(30)\n')
        self.assertTrue(run_checks(self.workspace,timeout=.5)['timed_out'])
        self.source('import unittest\nclass Good(unittest.TestCase):\n def test_ok(self):self.assertEqual(1,1)\n')
        result=run_checks(self.workspace);self.assertTrue(result['passed'],result);self.assertEqual(result['tests_run'],1)

    def test_invalid_time_limits_and_non_python_scope(self):
        for timeout in (0,-1,True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):run_checks(self.workspace,timeout=timeout)
        result=run_checks(self.workspace);self.assertTrue(result['passed']);self.assertEqual(result['tests_run'],0)
        self.assertIn('file review only',result['scope'])

    def test_fixed_acceptance_is_temporary_and_no_tests_cannot_pass(self):
        self.source('value=1\n')
        self.assertFalse(run_checks(self.workspace)['passed'])
        acceptance='import unittest\nclass Fixed(unittest.TestCase):\n def test_ok(self):self.assertTrue(True)\n'
        before={p.name for p in self.root.iterdir()}
        result=run_checks(self.workspace,acceptance);self.assertTrue(result['passed'],result)
        self.assertEqual({p.name for p in self.root.iterdir()},before)

    def test_timed_out_task_does_not_block_next_queued_task(self):
        store=Store(self.root/'data');save_team(store,PRESETS[0])
        hanging=store.add('hanging verification','ollama',max_revisions=0);next_task=store.add('next demo','demo')
        class Backend:
            def run(self,task,role,history,files,log):
                if role=='developer':files.execute({'tool':'write','path':'test_hang.py','content':'import time\ntime.sleep(30)\n'},True)
                return {'summary':'test backend','passed':True}
        def limited(folder,acceptance=''):return run_checks(folder,acceptance,timeout=.5)
        worker=Worker(store)
        with patch('app.Ollama',return_value=Backend()),patch('app.run_checks',side_effect=limited):
            worker.tick();worker.tick()
        states={t['id']:t for t in store.snapshot()['tasks']}
        self.assertEqual(states[hanging]['status'],'failed');self.assertEqual(states[next_task]['status'],'completed')
        report=store.root/'tasks'/hanging
        self.assertTrue(json.loads((report/'report.json').read_text(encoding='utf-8'))['verification']['timed_out'])
        self.assertIn('제한 시간 초과',(report/'report.md').read_text(encoding='utf-8'))
