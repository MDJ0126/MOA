from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from supervisor import write_status


class StatusFileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.target=self.root/'status.json'

    def test_existing_temporary_file_is_not_overwritten(self):
        old=self.root/'status.json.tmp';old.write_bytes(b'other-writer-record')
        self.assertTrue(write_status(self.target,{'writer':1}))
        self.assertEqual(old.read_bytes(),b'other-writer-record')
        self.assertEqual(json.loads(self.target.read_text()),{'writer':1})

    def test_linked_temporary_file_does_not_modify_original(self):
        original=self.root/'original';original.write_bytes(b'keep-this-content')
        (self.root/'status.json.tmp').hardlink_to(original)
        self.assertTrue(write_status(self.target,{'writer':1}))
        self.assertEqual(original.read_bytes(),b'keep-this-content')

    def test_failed_publication_preserves_status_and_cleans_owned_temporary_files(self):
        self.target.write_text('{"writer":"original"}')
        with patch.object(Path,'replace',side_effect=PermissionError('reader')),patch('supervisor.time.sleep'):
            self.assertFalse(write_status(self.target,{'writer':'new'}))
        self.assertEqual(json.loads(self.target.read_text()),{'writer':'original'})
        self.assertEqual(list(self.root.iterdir()),[self.target])

    def test_concurrent_writers_leave_one_complete_status(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda value:write_status(self.target,{'writer':value,'text':str(value)*1000}),range(12)))
        self.assertTrue(all(results))
        record=json.loads(self.target.read_text())
        self.assertIn(record['writer'],range(12));self.assertEqual(record['text'],str(record['writer'])*1000)
        self.assertEqual(list(self.root.iterdir()),[self.target])

    def test_temporary_name_collision_is_not_overwritten_or_deleted(self):
        original=self.root/'original';original.write_bytes(b'keep-this-content')
        collision=self.root/'.status.json-fixed.tmp';collision.hardlink_to(original)
        with patch('supervisor.secrets.token_hex',return_value='fixed'),patch('supervisor.time.sleep'):
            self.assertFalse(write_status(self.target,{'writer':1}))
        self.assertEqual(original.read_bytes(),b'keep-this-content')
        self.assertTrue(collision.exists());self.assertFalse(self.target.exists())


if __name__=='__main__':unittest.main()
