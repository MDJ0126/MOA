import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from app import Store,Worker,Demo
from local_model import relevant_history
from setup_config import PRESETS,save_team
from reports import markdown,publish,workspace_files


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent)
        self.store=Store(self.temp.name);save_team(self.store,PRESETS[1])
        self.tid=self.store.add('Readable <script>alert(1)</script> result','demo')
        self.store.message(self.tid,'builder','Keep this instruction')
        self.worker=Worker(self.store);self.worker.tick()
        self.folder=Path(self.temp.name)/'tasks'/self.tid
        self.report=json.loads((self.folder/'report.json').read_text(encoding='utf-8'))
    def tearDown(self):self.temp.cleanup()

    def test_readable_snapshot_and_bundle_have_matching_evidence(self):
        rendered=(self.folder/'report.md').read_text(encoding='utf-8')
        self.assertIn('지우',rendered);self.assertIn('개발',rendered);self.assertIn('서연',rendered)
        self.assertIn('실제 AI 추론과 실행 테스트 없음',rendered)
        self.assertNotIn('<script>',rendered)
        self.assertNotIn('"content":',rendered)
        self.assertIn('| --- | --- |\n| ',rendered)
        with zipfile.ZipFile(self.folder/'artifacts.zip') as archive:
            saved=json.loads(archive.read('report.json'))
            self.assertEqual(saved['report_id'],self.report['report_id'])
            self.assertEqual(archive.read('report.md').decode('utf-8'),rendered)
            self.assertIn('agents/builder/messages.json',archive.namelist())
            for file in saved['files']:
                self.assertEqual(hashlib.sha256(archive.read('workspace/'+file['path'].replace('\\','/'))).hexdigest(),file['sha256'])

    def test_zero_tests_are_not_presented_as_executable_test_success(self):
        report=dict(self.report,mode='llama',verification={'passed':True,'tests_run':0,'scope':'file review only'})
        rendered=markdown(report)
        self.assertIn('자동 파일 검사만 통과 · 실행 테스트 없음',rendered)
        self.assertNotIn('실행 테스트 0개 통과',rendered)
        report['verification']={}
        self.assertIn('검증 미실시 또는 미확인',markdown(report))
        demo=dict(self.report,status='failed')
        self.assertIn('데모 흐름 미완료',markdown(demo))

    def test_failed_bundle_build_preserves_all_previous_reports(self):
        old={name:(self.folder/name).read_bytes() for name in ('report.md','report.json','artifacts.zip','agents/builder/messages.json')}
        with patch('reports.zipfile.ZipFile.write',side_effect=OSError('injected bundle failure')):
            with self.assertRaises(OSError):self.worker.package(self.tid,'failed')
        for name,content in old.items():self.assertEqual((self.folder/name).read_bytes(),content)
        self.assertEqual(list(self.folder.glob('.report-*')),[])

    def test_changed_source_is_detected_before_any_report_is_published(self):
        old=(self.folder/'artifacts.zip').read_bytes();copy=shutil.copyfile
        workspace=self.folder/'workspace'
        def changed(source,target,*args,**kwargs):
            if Path(source).parent==workspace:Path(source).write_text('changed during snapshot',encoding='utf-8')
            return copy(source,target,*args,**kwargs)
        with patch('reports.shutil.copyfile',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'내용이 변경'):publish(self.folder,workspace,self.report)
        self.assertEqual((self.folder/'artifacts.zip').read_bytes(),old)

    def test_file_changed_after_verification_is_reported_as_unverified(self):
        tid=self.store.add('change after checks','demo');worker=Worker(self.store);package=worker.package
        def changed(task_id,status,*args,**kwargs):
            if status=='completed':
                workspace=Path(self.temp.name)/'tasks'/task_id/'workspace'
                next(workspace.iterdir()).write_text('changed after verification',encoding='utf-8')
            return package(task_id,status,*args,**kwargs)
        with patch.object(worker,'package',side_effect=changed):worker.tick()
        folder=Path(self.temp.name)/'tasks'/tid
        report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'failed')
        self.assertFalse(report['verification_matches_files'])
        self.assertIn('현재 파일 재검증 필요',(folder/'report.md').read_text(encoding='utf-8'))

    def test_link_classification_is_rejected_before_file_reads(self):
        workspace=self.folder/'workspace';original=Path.is_symlink
        target=next(workspace.iterdir())
        with patch.object(Path,'is_symlink',lambda path:True if path==target else original(path)):
            with self.assertRaisesRegex(ValueError,'링크'):workspace_files(workspace)

    def test_workspace_archive_filename_is_not_confused_with_outer_bundle(self):
        (self.folder/'workspace/artifacts.zip').write_bytes(b'user archive file')
        self.worker.package(self.tid,'failed')
        with zipfile.ZipFile(self.folder/'artifacts.zip') as archive:
            self.assertEqual(archive.read('workspace/artifacts.zip'),b'user archive file')


class RepairContextTests(unittest.TestCase):
    def test_recent_three_user_instructions_share_a_bounded_context(self):
        history=[{'agent':'user','content':json.dumps({'recipient':'builder','summary':f'user-{i}'})} for i in range(5)]
        history.extend({'agent':'checker','content':json.dumps({'summary':str(i)})} for i in range(20))
        context=relevant_history(history,'developer','builder')
        self.assertEqual([m['summary'] for m in context if m['agent']=='user'],['user-2','user-3','user-4'])
        self.assertEqual(len(context),6)

    def test_related_repair_handoffs_do_not_evict_user_instruction(self):
        history=[{'agent':'user','recipient':'builder','content':json.dumps({'summary':'KEEP'})}]
        history.extend({'agent':'checker','content':json.dumps({'summary':str(i)})} for i in range(20))
        context=relevant_history(history,'developer','builder')
        self.assertEqual(context[0]['summary'],'KEEP');self.assertEqual(context[-1]['summary'],'19');self.assertLessEqual(len(context),6)

    def test_malformed_other_recipient_instruction_is_not_included(self):
        self.assertEqual(relevant_history([{'agent':'user','recipient':'checker','content':'raw private instruction'}],'developer','builder'),[])

    def test_real_repair_loop_retains_addressed_instruction(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as folder:
            store=Store(folder);save_team(store,PRESETS[0]);tid=store.add('repair loop','demo',max_revisions=5)
            store.message(tid,'maker','PERSISTENT_REQUIREMENT')
            original=Demo.run;seen=[]
            def run(backend,task,role,history,*args):
                if role=='developer':
                    seen.append(relevant_history(history,role,'maker'))
                if role=='reviewer' and len(seen)<5:return {'summary':'repair required','passed':False}
                return original(backend,task,role,history,*args)
            with patch.object(Demo,'run',run):
                worker=Worker(store)
                for _ in range(5):worker.tick()
            self.assertEqual(store.snapshot()['tasks'][0]['status'],'completed')
            self.assertEqual(len(seen),5)
            self.assertTrue(all(any(m.get('summary')=='PERSISTENT_REQUIREMENT' for m in history) for history in seen))
