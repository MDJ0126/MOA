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
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data')
        save_team(self.store,PRESETS[0]);self.tid=self.store.add('artifact delivery','demo');Worker(self.store).tick()
        self.plan={'agents':PRESETS[0]['agents']}
    def tearDown(self):self.temp.cleanup()

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
