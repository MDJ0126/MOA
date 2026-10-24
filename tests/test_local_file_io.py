from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app import LocalFiles


class LocalFileIOTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.files=LocalFiles(self.root/'workspace')

    def write(self,name,content):return self.files.execute({'tool':'write','path':name,'content':content},True)
    def read(self,name):return self.files.execute({'tool':'read','path':name},False)

    def test_old_temporary_alias_does_not_modify_outside_original(self):
        original=self.root/'original';original.write_text('keep-original',encoding='utf-8')
        alias=self.files.folder/'target.py.agent-tmp';alias.hardlink_to(original)
        self.write('target.py','value=1\n')
        self.assertEqual(original.read_text(),'keep-original');self.assertTrue(alias.exists())
        self.assertEqual(self.read('target.py'),'value=1\n')

    def test_existing_temporary_user_file_is_preserved(self):
        old=self.files.folder/'target.py.agent-tmp';old.write_text('user-data')
        self.write('target.py','value=2\n')
        self.assertEqual(old.read_text(),'user-data')

    def test_failed_publication_keeps_target_and_removes_owned_temp(self):
        target=self.files.folder/'target.py';target.write_text('original')
        with patch.object(Path,'replace',side_effect=PermissionError('reader')):
            with self.assertRaises(PermissionError):self.write('target.py','replacement')
        self.assertEqual(target.read_text(),'original');self.assertEqual(list(self.files.folder.iterdir()),[target])

    def test_hardlink_read_is_rejected(self):
        original=self.root/'original';original.write_text('private-fixture')
        (self.files.folder/'alias.txt').hardlink_to(original)
        with self.assertRaises(ValueError):self.read('alias.txt')
        self.assertEqual(original.read_text(),'private-fixture')

    def test_newlines_preserve_read_semantics_without_expanding_write_budget(self):
        self.write('lines.txt','\n'*150000)
        self.assertLessEqual((self.files.folder/'lines.txt').stat().st_size,200000)
        self.assertEqual(self.read('lines.txt'),'\n'*150000)
        self.write('unicode.txt','가\r\n나\r다\n')
        self.assertEqual(self.read('unicode.txt'),'가\n나\n다\n')

    def test_oversized_read_is_rejected(self):
        (self.files.folder/'large.txt').write_bytes(b'x'*200001)
        with self.assertRaises(ValueError):self.read('large.txt')

    def test_temporary_name_collision_preserves_existing_link(self):
        original=self.root/'original';original.write_text('keep-original')
        collision=self.files.folder/'.agent-fixed.tmp';collision.hardlink_to(original)
        with patch('app.uuid.uuid4',return_value=SimpleNamespace(hex='fixed')):
            with self.assertRaises(FileExistsError):self.write('target.txt','replacement')
        self.assertEqual(original.read_text(),'keep-original');self.assertTrue(collision.exists())
        self.assertFalse((self.files.folder/'target.txt').exists())

    def test_replaced_opened_file_is_rejected_and_handle_is_closed(self):
        target=self.files.folder/'target.txt';target.write_text('public-fixture')
        outside=self.root/'outside';outside.write_text('private-fixture')
        opened=[];native_open=Path.open
        def swapped(path,*args,**kwargs):
            stream=native_open(outside if path==target else path,*args,**kwargs)
            opened.append(stream);return stream
        with patch.object(Path,'open',new=swapped):
            with self.assertRaises(ValueError):self.read('target.txt')
        self.assertTrue(opened);self.assertTrue(all(stream.closed for stream in opened))

    def test_growth_after_initial_stat_still_obeys_byte_limit(self):
        target=self.files.folder/'target.txt';target.write_bytes(b'x');native_open=Path.open
        def growing(path,*args,**kwargs):
            stream=native_open(path,*args,**kwargs)
            if path==target:
                with native_open(path,'ab') as writer:writer.write(b'x'*200001)
            return stream
        with patch.object(Path,'open',new=growing):
            with self.assertRaisesRegex(ValueError,'한도'):self.read('target.txt')


if __name__=='__main__':unittest.main()
