from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo

class InstructionCorruptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.worker=Worker(self.store)
    def test_bad_pending_stage_is_held_before_backend_and_next_task_runs(self):
        tid=self.store.add('bad instruction stage','demo');next_id=self.store.add('next good task','demo')
        with self.store.connect() as c:c.execute('UPDATE tasks SET pending_instruction_stage=? WHERE id=?',('broken',tid))
        with patch.object(Demo,'run',side_effect=AssertionError('backend must not run')):self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'interrupted')
        self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(next_id,)).fetchone()[0],'completed')
    def test_queued_review_hold_is_not_executed_without_manual_resume(self):
        tid=self.store.add('review marker retained','demo')
        with self.store.connect() as c:c.execute('UPDATE tasks SET instruction_review_required=1 WHERE id=?',(tid,))
        with patch.object(Demo,'run',side_effect=AssertionError('backend must not run')):self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'interrupted')

    def test_malformed_metadata_blocks_retry_message_and_recovery(self):
        cases=[('pending_instruction_stage',value) for value in (-1,4,1.5,'broken',b'1')]+[('instruction_review_required',value) for value in (None,-1,2,.5,'broken',b'0')]
        for index,(field,value) in enumerate(cases):
            with self.subTest(field=field,value=value):
                tid=self.store.add('malformed instruction metadata '+str(index),'demo')
                with self.store.connect() as c:c.execute('UPDATE tasks SET '+field+'=? WHERE id=?',(value,tid))
                with patch.object(Demo,'run',side_effect=AssertionError('backend must not run')):self.worker.tick()
                with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'interrupted')
                with self.assertRaises(ValueError):self.store.control('retry',tid)
                with self.assertRaises(ValueError):self.store.message(tid,'developer','must not repair metadata',request_key='corrupt-message-'+str(index))
                with self.store.connect() as c:
                    self.assertEqual(c.execute('SELECT '+field+' FROM tasks WHERE id=?',(tid,)).fetchone()[0],value)
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE task_id=? AND sender='user'",(tid,)).fetchone()[0],0)
                    c.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
                self.worker.recover()
                with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'interrupted')
    def test_corruption_during_backend_is_held_without_handoff(self):
        tid=self.store.add('corruption during stage','demo');run=Demo.run
        def corrupt(backend,task,role,*args):
            if role=='developer':
                with self.store.connect() as c:c.execute('UPDATE tasks SET pending_instruction_stage=? WHERE id=?',('broken',tid))
            return run(backend,task,role,*args)
        with patch.object(Demo,'run',corrupt):self.worker.tick()
        with self.store.connect() as c:
            status,error=c.execute('SELECT status,error FROM tasks WHERE id=?',(tid,)).fetchone();self.assertEqual(status,'interrupted');self.assertIn('손상',error)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE task_id=? AND sender='developer'",(tid,)).fetchone()[0],0)
        next_id=self.store.add('next after runtime corruption','demo');self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(next_id,)).fetchone()[0],'completed')
    def test_restored_corrupt_metadata_is_preserved_but_not_executed(self):
        from data_backup import backup,restore
        tid=self.store.add('restored corrupt instruction','demo')
        with self.store.connect() as c:c.execute('UPDATE tasks SET pending_instruction_stage=-1 WHERE id=?',(tid,))
        root=Path(self.temp.name);destination=root/'restored'
        with tempfile.TemporaryDirectory(dir=root.parent) as archive_folder:
            archive=Path(archive_folder)/'archive.zip';backup(root,archive);restore(archive,destination)
        restored=Store(destination);worker=Worker(restored);restored.control('resume')
        with patch.object(Demo,'run',side_effect=AssertionError('backend must not run')):worker.tick()
        with restored.connect() as c:self.assertEqual(tuple(c.execute('SELECT status,pending_instruction_stage FROM tasks WHERE id=?',(tid,)).fetchone()),('interrupted',-1))
    def test_invalid_hold_audit_failure_rolls_back_state_change(self):
        import sqlite3
        tid=self.store.add('audit corruption hold','demo')
        with self.store.connect() as c:
            c.execute('UPDATE tasks SET pending_instruction_stage=-1 WHERE id=?',(tid,))
            c.execute("CREATE TRIGGER reject_corruption_hold BEFORE INSERT ON messages WHEN NEW.kind='error' BEGIN SELECT RAISE(ABORT,'fixture audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.worker.tick()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'queued')
    def test_valid_review_hold_can_be_manually_resumed(self):
        tid=self.store.add('valid review hold resume','demo')
        with self.store.connect() as c:c.execute('UPDATE tasks SET instruction_review_required=1 WHERE id=?',(tid,))
        self.worker.tick();self.store.control('retry',tid);self.worker.tick()
        with self.store.connect() as c:self.assertEqual(tuple(c.execute('SELECT status,instruction_review_required FROM tasks WHERE id=?',(tid,)).fetchone()),('completed',0))

    def test_binary_instruction_metadata_does_not_break_task_json(self):
        import json
        tid=self.store.add('binary metadata task view','demo')
        with self.store.connect() as c:c.execute('UPDATE tasks SET pending_instruction_stage=?,instruction_review_required=? WHERE id=?',(b'PRIVATE_BLOB_MARKER',b'0',tid))
        self.worker.tick()
        encoded=json.dumps(self.store.snapshot(),ensure_ascii=False)
        self.assertNotIn('PRIVATE_BLOB_MARKER',encoded)
        task=self.store.snapshot()['tasks'][0];self.assertEqual(task['invalid_instruction_fields'],['pending_instruction_stage','instruction_review_required'])
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT pending_instruction_stage FROM tasks WHERE id=?',(tid,)).fetchone()[0],b'PRIVATE_BLOB_MARKER')

if __name__=='__main__':unittest.main()
