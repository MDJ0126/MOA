from pathlib import Path
import tempfile
import unittest
from app import LocalFiles,Store


class FilePathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.files=LocalFiles(self.root/'workspace');self.store=Store(self.root/'data')

    def test_reserved_and_invalid_names_are_rejected_without_opening_files(self):
        for name in ('NUL','nul.txt','CON','sub/AUX.py','COM1.log','LPT9.txt','a?b.py','a|b.py','a*b.py','a"b.py','a\x00b.py','a\nb.py','folder//file.py','../outside.py','sub/../outside.py','/outside.py','C:\\outside.py','trailing.','trailing '):
            with self.subTest(name=repr(name)),self.assertRaises(ValueError):self.files.path(name)
        self.assertEqual(list(self.files.folder.iterdir()),[])

    def test_invalid_required_paths_are_rejected_before_task_acceptance(self):
        for name in ('NUL.txt','sub/CON.py','../outside.py','a?b.py'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.store.add('invalid required path','demo',required_files=[name])
        self.assertEqual(self.store.snapshot()['tasks'],[])

    def test_valid_unicode_dotfiles_and_similar_names_remain_supported(self):
        for name in ('src/health.py','.gitignore','알림/도움.txt','CONTEXT.md','com10.txt','auxiliary.py'):
            with self.subTest(name=name):self.assertTrue(self.files.path(name).is_relative_to(self.files.folder))
        self.assertEqual(self.files.path('src\\health.py'),self.files.path('src/health.py'))
        self.assertTrue(self.store.add('valid required path','demo',required_files=['src/health.py']))

    def test_directories_are_not_read_or_overwritten_as_files(self):
        folder=self.files.folder/'directory';folder.mkdir();(folder/'keep.txt').write_text('original')
        with self.assertRaises(ValueError):self.files.execute({'tool':'read','path':'directory'},False)
        with self.assertRaises(ValueError):self.files.execute({'tool':'write','path':'directory','content':'replacement'},True)
        self.assertEqual((folder/'keep.txt').read_text(),'original');self.assertEqual(list(self.files.folder.iterdir()),[folder])


if __name__=='__main__':unittest.main()
