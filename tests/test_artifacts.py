import io
import os
from pathlib import Path
import tempfile
import unittest
from artifacts import ArtifactMissing,CHUNK_SIZE,chunks,open_artifact,metadata
from app import Store,Worker
from setup_config import PRESETS,save_team


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data')
        save_team(self.store,PRESETS[0]);self.tid=self.store.add('artifact delivery','demo');Worker(self.store).tick()
        self.plan={'agents':PRESETS[0]['agents']}
    def tearDown(self):self.temp.cleanup()

    def test_running_research_image_is_available_before_final_publication(self):
        import hashlib,json
        folder=self.store.root/'tasks'/self.tid/'workspace/research';folder.mkdir(parents=True,exist_ok=True);body=b'\xff\xd8\xfflive-image'
        (folder/'image.jpg').write_bytes(body);(folder/'sources.json').write_text(json.dumps({'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}))
        with self.store.connect() as c:c.execute("UPDATE tasks SET status='running',artifact_publication='{}' WHERE id=?",(self.tid,))
        with open_artifact(self.store.root,self.tid,'workspace/research/image.jpg',self.plan) as (stream,info):self.assertEqual(stream.read(),body)
        (folder/'image.jpg').write_bytes(b'tampered')
        with self.assertRaises(ArtifactMissing):
            with open_artifact(self.store.root,self.tid,'workspace/research/image.jpg',self.plan):pass

    def test_stream_reads_are_bounded_and_stop_at_snapshot_size(self):
        class Stream(io.BytesIO):
            def read(self,size=-1):
                if not 0<size<=CHUNK_SIZE:raise AssertionError('unbounded read')
                return super().read(size)
        size=CHUNK_SIZE*3+17
        result=list(chunks(Stream(b'A'*size+b'not part of snapshot'),size))
        self.assertEqual([len(part) for part in result],[CHUNK_SIZE]*3+[17])

    def test_metadata_and_open_file_agree(self):
        files=metadata(self.store.root,self.tid,self.plan)['files']
        self.assertTrue(any(f['name']=='artifacts.zip' for f in files))
        for item in files:
            with open_artifact(self.store.root,self.tid,item['name'],self.plan) as (stream,info):
                self.assertEqual(sum(map(len,chunks(stream,info.st_size))),item['bytes'])

    def test_handoff_keeps_the_actual_delivery_attachments(self):
        import json
        content={'summary':'shared result','images':['vm/r0-developer-after.png'],'files':['deliverables/report.md']}
        self.store.deliver(self.tid,'developer','reviewer',content,handoff={'summary':'peer handoff'})
        with self.store.connect() as c:row=c.execute("SELECT content FROM messages WHERE task_id=? AND kind='handoff' ORDER BY seq DESC LIMIT 1",(self.tid,)).fetchone()
        handoff=json.loads(row[0]);self.assertEqual(handoff['summary'],'peer handoff');self.assertEqual(handoff['images'],content['images']);self.assertEqual(handoff['files'],content['files'])

    def test_registered_attachments_only_are_previewable(self):
        folder=self.store.root/'tasks'/self.tid/'attachments';folder.mkdir()
        name='attachments/'+('a'*64)+'.png';(folder/('a'*64+'.png')).write_bytes(b'\x89PNG\r\n\x1a\n')
        with self.assertRaises(ArtifactMissing):
            with open_artifact(self.store.root,self.tid,name,self.plan):pass
        with self.store.connect() as c:c.execute('INSERT INTO slack_attachments VALUES(?,?,?,?,?)',('event','FTEST123',self.tid,name,'image/png'))
        with open_artifact(self.store.root,self.tid,name,self.plan) as (stream,info):self.assertTrue(stream.read().startswith(b'\x89PNG'))
        self.assertIn(name,{f['name'] for f in metadata(self.store.root,self.tid,self.plan)['files']})

    def test_html_preview_is_text_only_and_bounded(self):
        import json,hashlib
        folder=self.store.root/'tasks'/self.tid/'deliverables';folder.mkdir(exist_ok=True)
        body=b'<html><style>SECRET_STYLE</style><script>alert(1)</script><body>Human report '+b'x'*10000+b'</body></html>'
        (folder/'report.html').write_bytes(body)
        publication={'report_id':'a'*32,'files':{'deliverables/report.html':{'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}}}
        with self.store.connect() as c:c.execute('UPDATE tasks SET artifact_publication=? WHERE id=?',(json.dumps(publication),self.tid))
        item=next(f for f in metadata(self.store.root,self.tid,self.plan)['files'] if f['name']=='deliverables/report.html')
        self.assertIn('Human report',item['preview']);self.assertNotIn('alert',item['preview']);self.assertNotIn('SECRET_STYLE',item['preview']);self.assertLessEqual(len(item['preview']),1600)

    def test_path_aliases_and_unlisted_files_are_rejected(self):
        for name in ('../model.key','report.md:stream','agents/../../model.key','/report.md','vm/screen.png','unknown.json'):
            with self.assertRaises(FileNotFoundError):
                with open_artifact(self.store.root,self.tid,name,self.plan):pass

    def test_actual_hardlink_to_outside_task_is_not_served(self):
        secret=self.root/'private-test.txt';secret.write_text('PRIVATE_TEST_MARKER')
        report=self.store.root/'tasks'/self.tid/'report.md';report.unlink();os.link(secret,report)
        with self.assertRaises(ArtifactMissing):
            with open_artifact(self.store.root,self.tid,'report.md',self.plan):pass
        self.assertNotIn('report.md',{f['name'] for f in metadata(self.store.root,self.tid,self.plan)['files']})

    def test_workspace_hardlink_is_not_copied_into_result_bundle(self):
        secret=self.root/'private-workspace-test.txt';secret.write_text('PRIVATE_TEST_MARKER')
        folder=self.store.root/'tasks'/self.tid;old=(folder/'artifacts.zip').read_bytes()
        os.link(secret,folder/'workspace/shared.txt')
        with self.assertRaisesRegex(ValueError,'하드링크'):Worker(self.store).package(self.tid,'failed')
        self.assertEqual((folder/'artifacts.zip').read_bytes(),old)

    def test_uncommitted_or_tampered_report_is_not_served(self):
        import json
        report=self.store.root/'tasks'/self.tid/'report.md';report.write_text('changed after publication',encoding='utf-8')
        with self.assertRaises(ArtifactMissing):
            with open_artifact(self.store.root,self.tid,'report.md',self.plan):pass
        self.assertNotIn('report.md',{item['name'] for item in metadata(self.store.root,self.tid,self.plan)['files']})
        with self.store.connect() as c:c.execute("UPDATE tasks SET artifact_publication='{}' WHERE id=?",(self.tid,))
        self.assertEqual(metadata(self.store.root,self.tid,self.plan)['files'],[])
    def test_legacy_reports_remain_readable_but_bad_receipts_fail_closed(self):
        import json
        with self.store.connect() as c:c.execute('UPDATE tasks SET artifact_publication=NULL WHERE id=?',(self.tid,))
        with open_artifact(self.store.root,self.tid,'report.json',self.plan) as (stream,info):self.assertGreater(info.st_size,0)
        for value in ('not JSON',json.dumps({'report_id':'a'*32,'files':[]})):
            with self.store.connect() as c:c.execute('UPDATE tasks SET artifact_publication=? WHERE id=?',(value,self.tid))
            with self.assertRaises(ArtifactMissing):
                with open_artifact(self.store.root,self.tid,'report.json',self.plan):pass
