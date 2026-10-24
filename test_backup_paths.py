from pathlib import Path
import tempfile
import unittest
import hashlib
import json
import os
import zipfile
from unittest.mock import patch
from app import Store
from data_backup import backup,restore


class BackupPathConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'data';Store(self.source);self.archive=self.root/'backup.zip'
    def test_case_alias_candidates_do_not_publish_unrestorable_backup(self):
        # Simulate names from a case-sensitive file provider; no host FS mode change.
        upper=self.source/'Folder';lower=self.source/'folder';upper.mkdir();lower.mkdir(exist_ok=True)
        a=upper/'a.txt';b=lower/'b.txt';a.write_bytes(b'a');b.write_bytes(b'b')
        with patch.object(Path,'rglob',return_value=[self.source/'state.sqlite3',a,b]):
            with self.assertRaisesRegex(ValueError,'충돌'):backup(self.source,self.archive)
        self.assertFalse(self.archive.exists());self.assertEqual(a.read_bytes(),b'a');self.assertEqual(b.read_bytes(),b'b')
    def test_duplicate_canonical_database_candidate_is_not_published(self):
        database=self.source/'state.sqlite3'
        with patch.object(Path,'rglob',return_value=[database,database]):
            with self.assertRaisesRegex(ValueError,'중복'):backup(self.source,self.archive)
        self.assertFalse(self.archive.exists());self.assertTrue(database.is_file())
    def test_file_replaced_at_open_is_not_copied_or_published(self):
        candidate=self.source/'result.txt';candidate.write_bytes(b'expected')
        outside=self.root/'outside.txt';outside.write_bytes(b'unrelated private file')
        handle=outside.open('rb');original_open=Path.open
        def swapped_open(path,*args,**kwargs):
            return handle if path==candidate else original_open(path,*args,**kwargs)
        with patch.object(Path,'open',swapped_open):
            with self.assertRaisesRegex(ValueError,'변경'):backup(self.source,self.archive)
        self.assertTrue(handle.closed);self.assertFalse(self.archive.exists())
        self.assertEqual(candidate.read_bytes(),b'expected');self.assertEqual(outside.read_bytes(),b'unrelated private file')
    def test_normal_nested_paths_still_roundtrip(self):
        folder=self.source/'tasks/task-one/workspace';folder.mkdir(parents=True);(folder/'example.py').write_bytes(b'answer=42\n')
        backup(self.source,self.archive);restore(self.archive,self.root/'restored')
        self.assertEqual((self.root/'restored/tasks/task-one/workspace/example.py').read_bytes(),b'answer=42\n')
    def test_file_growth_during_read_does_not_publish_backup(self):
        self.assert_changed_file_not_published('growth')
    def test_same_size_edit_during_read_does_not_publish_backup(self):
        self.assert_changed_file_not_published('same-size')
    def assert_changed_file_not_published(self,change):
        candidate=self.source/'result.txt';candidate.write_bytes(b'original')
        original_stat=candidate.stat()
        original_open=Path.open;handles=[]
        class ChangingReader:
            def __init__(self,stream):self.stream=stream;self.changed=False
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def fileno(self):return self.stream.fileno()
            def read(self,size):
                value=self.stream.read(size)
                if not self.changed:
                    self.changed=True
                    with original_open(candidate,'ab' if change=='growth' else 'wb') as writer:
                        writer.write(b' changed' if change=='growth' else b'updated!')
                    if change=='same-size':os.utime(candidate,ns=(original_stat.st_atime_ns,original_stat.st_mtime_ns+1_000_000_000))
                return value
        def changing_open(path,*args,**kwargs):
            stream=original_open(path,*args,**kwargs)
            if path==candidate:handles.append(stream);return ChangingReader(stream)
            return stream
        with patch.object(Path,'open',changing_open):
            with self.assertRaisesRegex(ValueError,'변경'):backup(self.source,self.archive)
        self.assertFalse(self.archive.exists());self.assertTrue(all(handle.closed for handle in handles))
        self.assertEqual(candidate.read_bytes(),b'original changed' if change=='growth' else b'updated!')
    def test_nonportable_archive_names_are_rejected_before_writing(self):
        backup(self.source,self.archive)
        with zipfile.ZipFile(self.archive) as original:
            contents={name:original.read(name) for name in original.namelist()}
        for index,name in enumerate(('tasks/a?.txt','tasks/a*.txt','tasks/a<.txt','tasks/a>.txt','tasks/a|.txt','tasks/a".txt','tasks/control\x01.txt','tasks/CON .txt')):
            with self.subTest(name=name):
                manifest=json.loads(contents['manifest.json']);payload=b'example'
                manifest['files'][name]={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
                archive=self.root/f'invalid-{index}.zip';destination=self.root/f'restored-{index}'
                with zipfile.ZipFile(archive,'w') as output:
                    for entry,value in contents.items():
                        if entry!='manifest.json':output.writestr(entry,value)
                    output.writestr('manifest.json',json.dumps(manifest));output.writestr('data/'+name,payload)
                with self.assertRaisesRegex(ValueError,'백업 경로'):restore(archive,destination)
                self.assertFalse(destination.exists())
