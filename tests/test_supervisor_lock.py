import os
from pathlib import Path
import tempfile
import unittest
from supervisor import acquire


class SupervisorLockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'app.lock'

    def test_repeated_acquisition_does_not_grow_lock(self):
        for _ in range(4):
            with acquire(self.path):pass
        self.assertEqual(self.path.read_bytes(),b'0')

    def test_existing_contents_are_not_modified(self):
        self.path.write_bytes(b'existing-record')
        with acquire(self.path):pass
        self.assertEqual(self.path.read_bytes(),b'existing-record')

    def test_failed_contender_does_not_append(self):
        with acquire(self.path):
            with self.assertRaises(OSError):acquire(self.path)
        self.assertEqual(self.path.read_bytes(),b'0')
        with acquire(self.path):pass

    def test_hardlink_is_rejected_without_touching_original(self):
        original=self.root/'original';original.write_bytes(b'keep-this-content');os.link(original,self.path)
        with self.assertRaises(OSError):acquire(self.path)
        self.assertEqual(original.read_bytes(),b'keep-this-content')

    def test_directory_is_rejected(self):
        self.path.mkdir()
        with self.assertRaises(OSError):acquire(self.path)
        self.assertEqual(list(self.path.iterdir()),[])


if __name__=='__main__':unittest.main()
