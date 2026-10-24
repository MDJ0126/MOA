from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo

class InstructionUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.worker=Worker(self.store);self.tid=self.store.add('instruction update fixture','demo')
    def test_new_instruction_to_completed_task_is_not_silently_recorded(self):
        self.worker.tick()
        with self.assertRaises(ValueError):self.store.message(self.tid,'developer','change the completed result',request_key='completed-instruction')
    def test_instruction_arriving_during_stage_is_processed_on_next_attempt(self):
        run=Demo.run;added=False;seen=[]
        def inject(backend,task,role,history,files,log):
            nonlocal added
            if role=='developer':
                seen.append(str(history))
                if not added:added=True;self.store.message(self.tid,'developer','NEW_REQUIREMENT',request_key='during-stage-instruction')
            return run(backend,task,role,history,files,log)
        with patch.object(Demo,'run',inject):
            self.worker.tick();self.worker.tick()
        self.assertGreaterEqual(len(seen),2)
        self.assertIn('NEW_REQUIREMENT',seen[-1])
        with self.store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='developer'").fetchone()[0],1)

    def test_replay_after_completion_returns_receipt_without_requeue(self):
        first=self.store.message(self.tid,'developer','ONE_REQUEST',request_key='instruction-replay-key')
        self.assertEqual(first['application'],'pending_stage');self.worker.tick()
        receipt=self.store.message(self.tid,'developer','ONE_REQUEST',request_key='instruction-replay-key')
        self.assertEqual(receipt['application'],'already_recorded')
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],'completed')
    def test_failed_instruction_is_stored_for_manual_resume(self):
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='failed',stage=2 WHERE id=?",(self.tid,))
        receipt=self.store.message(self.tid,'planner','REVISED_PLAN',request_key='failed-instruction-key')
        self.assertEqual(receipt['application'],'resume_required')
        self.store.control('retry',self.tid)
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT stage FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],0)
        self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],'completed')
    def test_cancelled_task_does_not_accept_new_instruction(self):
        self.store.control('cancel',self.tid)
        with self.assertRaises(ValueError):self.store.message(self.tid,'developer','do not restart',request_key='cancelled-instruction-key')
    def test_past_stage_instruction_rewinds_before_final_handoff(self):
        run=Demo.run;added=False;plans=[]
        def inject(backend,task,role,history,files,log):
            nonlocal added
            if role=='planner':plans.append(str(history))
            if role=='reporter' and not added:added=True;self.store.message(self.tid,'planner','REPLAN_MARKER',request_key='late-plan-instruction')
            return run(backend,task,role,history,files,log)
        with patch.object(Demo,'run',inject):self.worker.tick();self.worker.tick()
        self.assertEqual(len(plans),2);self.assertIn('REPLAN_MARKER',plans[-1])
        with self.store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE sender='reporter'").fetchone()[0],1)
    def test_vm_rewind_is_held_across_restart_until_manual_resume(self):
        import json
        from contextlib import contextmanager
        run=Demo.run;added=False
        with self.store.connect() as c:
            plan=json.loads(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()[0]);plan['environment']={'kind':'vm','config':{},'actions':[{'role':'developer','action':'key','keys':['ret']}]}
            c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',(json.dumps(plan),self.tid))
        @contextmanager
        def fake_stage(task,role,actor,before=None):
            if before:before()
            yield
        def inject(backend,task,role,history,files,log):
            nonlocal added
            if role=='reviewer' and not added:added=True;self.store.message(self.tid,'planner','VM_REPLAN_MARKER',request_key='vm-plan-instruction')
            return run(backend,task,role,history,files,log)
        with patch.object(self.worker.vm,'stage',fake_stage),patch.object(Demo,'run',inject):self.worker.tick()
        self.worker.recover()
        with self.store.connect() as c:self.assertEqual(tuple(c.execute('SELECT status,stage,instruction_review_required FROM tasks WHERE id=?',(self.tid,)).fetchone()),('interrupted',0,1))
        self.store.control('retry',self.tid)
        with patch.object(self.worker.vm,'stage',fake_stage),patch.object(Demo,'run',inject):self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(self.tid,)).fetchone()[0],'completed')

if __name__=='__main__':unittest.main()
