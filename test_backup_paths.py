from pathlib import Path
import tempfile
import unittest
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
    def test_normal_nested_paths_still_roundtrip(self):
        folder=self.source/'tasks/task-one/workspace';folder.mkdir(parents=True);(folder/'example.py').write_bytes(b'answer=42\n')
        backup(self.source,self.archive);restore(self.archive,self.root/'restored')
        self.assertEqual((self.root/'restored/tasks/task-one/workspace/example.py').read_bytes(),b'answer=42\n')
