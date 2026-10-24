from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo


class WorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent)
        self.store=Store(self.temp.name);self.worker=Worker(self.store)

    def tearDown(self):self.temp.cleanup()

    def test_cancel_between_pause_observation_and_requeue_is_preserved(self):
        self.check_cancel_boundary(False)

    def test_cancel_between_stop_observation_and_requeue_is_preserved(self):
        self.check_cancel_boundary(True)

    def check_cancel_boundary(self,stopping):
        tid=self.store.add('cancel at yield boundary','demo');original_connect=self.store.connect;original_run=Demo.run;store=self.store;worker=self.worker
        fired=[]
        class Connection:
            def __init__(self,connection):self.connection=connection
            def execute(self,sql,parameters=()):
                if sql.startswith("UPDATE tasks SET status='queued' WHERE id=?") and not fired:
                    fired.append(True);store.control('cancel',tid,_connection=self.connection)
                return self.connection.execute(sql,parameters)
            def __getattr__(self,name):return getattr(self.connection,name)
        @contextmanager
        def intercept():
            with original_connect() as connection:yield Connection(connection)
        def run(backend,task,role,*args):
            if role=='planner':
                if stopping:worker.stop.set()
                else:store.control('pause')
            return original_run(backend,task,role,*args)
        with patch.object(self.store,'connect',intercept),patch.object(Demo,'run',run):self.worker.tick()
        self.assertTrue(fired)
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'cancelled')
        self.worker.stop.clear();self.store.control('resume');self.assertFalse(self.worker.tick())

    def test_separate_connection_cancel_survives_atomic_pause_yield(self):
        from concurrent.futures import ThreadPoolExecutor
        tid=self.store.add('concurrent cancellation at pause','demo');original_connect=self.store.connect;original_run=Demo.run;store=self.store;futures=[]
        with ThreadPoolExecutor(max_workers=1) as pool:
            class Connection:
                def __init__(self,connection):self.connection=connection
                def execute(self,sql,parameters=()):
                    if sql.startswith("UPDATE tasks SET status='queued' WHERE id=?") and not futures:futures.append(pool.submit(store.control,'cancel',tid))
                    return self.connection.execute(sql,parameters)
                def __getattr__(self,name):return getattr(self.connection,name)
            @contextmanager
            def intercept():
                with original_connect() as connection:yield Connection(connection)
            def run(backend,task,role,*args):
                if role=='planner':store.control('pause')
                return original_run(backend,task,role,*args)
            with patch.object(store,'connect',intercept),patch.object(Demo,'run',run):self.worker.tick()
            self.assertEqual(len(futures),1);futures[0].result(timeout=10)
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'cancelled')

    def test_corrupt_queued_plan_is_held_without_backend_and_next_task_runs(self):
        tid=self.store.add('corrupt plan','demo');next_id=self.store.add('next task','demo')
        with self.store.connect() as c:c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',('{invalid',tid))
        with patch.object(Demo,'run',side_effect=AssertionError('backend must not run')):self.worker.tick()
        states={t['id']:t for t in self.store.snapshot()['tasks']}
        self.assertEqual(states[tid]['status'],'interrupted');self.assertEqual(states[next_id]['status'],'queued')
        self.assertFalse((Path(self.temp.name)/'tasks'/tid/'workspace').exists())
        self.worker.tick();self.assertEqual(next(t for t in self.store.snapshot()['tasks'] if t['id']==next_id)['status'],'completed')

    def test_invalid_assignments_environment_and_stage_are_held_and_retry_rejected(self):
        for change in ('assignment','pipeline','missing_pipeline','environment','actions','stage','mode'):
            with self.subTest(change=change):
                tid=self.store.add(change,'demo')
                with self.store.connect() as c:
                    row=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone());plan=json.loads(row['execution_plan'])
                    if change=='assignment':plan['assignment']['reviewer']='planner'
                    if change=='pipeline':plan['pipeline']=[]
                    if change=='missing_pipeline':del plan['pipeline']
                    if change=='environment':plan['environment']={'kind':'unknown'}
                    if change=='actions':plan['environment']={'kind':'local','actions':[{'role':'developer','action':'unsupported'}]}
                    c.execute('UPDATE tasks SET execution_plan=?,stage=?,mode=? WHERE id=?',(json.dumps(plan),99 if change=='stage' else 0,'unknown' if change=='mode' else 'demo',tid))
                with patch.object(Demo,'run',side_effect=AssertionError('invalid task reached backend')):self.worker.tick()
                self.assertEqual(next(t for t in self.store.snapshot()['tasks'] if t['id']==tid)['status'],'interrupted')
                with self.assertRaisesRegex(ValueError,'실행 계획'):self.store.control('retry',tid)
                with self.store.connect() as c:c.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
                self.worker.recover()
                self.assertEqual(next(t for t in self.store.snapshot()['tasks'] if t['id']==tid)['status'],'interrupted')

    def test_invalid_plan_hold_and_history_roll_back_together(self):
        import sqlite3
        tid=self.store.add('hold history','demo')
        with self.store.connect() as c:
            c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',('{invalid',tid))
            c.execute("CREATE TRIGGER reject_hold BEFORE INSERT ON messages WHEN NEW.kind='error' BEGIN SELECT RAISE(ABORT,'history failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.worker.tick()
        self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'queued')
        with self.store.connect() as c:c.execute('DROP TRIGGER reject_hold')
        self.worker.tick();self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'interrupted')

    def test_legacy_plan_remains_executable(self):
        tid=self.store.add('legacy work','demo')
        with self.store.connect() as c:c.execute("UPDATE tasks SET execution_plan='' WHERE id=?",(tid,))
        self.worker.tick();self.assertEqual(self.store.snapshot()['tasks'][0]['status'],'completed')

    def test_nonfinite_plan_metadata_is_held_before_workspace_or_backend(self):
        for index,number in enumerate(('NaN','Infinity','-Infinity','1e309')):
            with self.subTest(number=number):
                self.store=Store(Path(self.temp.name)/str(index));self.worker=Worker(self.store)
                tid=self.store.add('invalid numeric metadata','demo');next_id=self.store.add('next healthy task','demo')
                with self.store.connect() as c:
                    plan=json.loads(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0])
                    plan['legacy_metadata']={'limit':'NUMERIC_MARKER'}
                    raw=json.dumps(plan).replace('"NUMERIC_MARKER"',number)
                    c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',(raw,tid))
                with patch.object(Demo,'run',side_effect=AssertionError('invalid plan reached backend')) as backend:self.worker.tick()
                backend.assert_not_called()
                self.assertEqual(next(task for task in self.store.snapshot()['tasks'] if task['id']==tid)['status'],'interrupted')
                self.assertFalse((self.store.root/'tasks'/tid/'workspace').exists())
                with self.store.connect() as c:self.assertEqual(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0],raw)
                with self.assertRaisesRegex(ValueError,'실행 계획'):self.store.control('retry',tid)
                self.worker.tick();self.assertEqual(next(task for task in self.store.snapshot()['tasks'] if task['id']==next_id)['status'],'completed')

    def test_workspace_preparation_failure_is_terminal_and_next_task_runs(self):
        tid=self.store.add('workspace failure','demo');next_id=self.store.add('next workspace','demo')
        with patch('app.LocalFiles',side_effect=PermissionError('workspace unavailable')):self.worker.tick()
        self.assertEqual(next(t for t in self.store.snapshot()['tasks'] if t['id']==tid)['status'],'failed')
        self.worker.tick();self.assertEqual(next(t for t in self.store.snapshot()['tasks'] if t['id']==next_id)['status'],'completed')
