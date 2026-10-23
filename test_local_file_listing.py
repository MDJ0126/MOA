import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app import LocalFiles


class LocalFileListingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.files=LocalFiles(self.root/'workspace')

    def counted(self,reparse_name=None):
        native=os.scandir;count=[0]
        class Reparse:
            def __init__(self,entry):self.entry=entry
            def __getattr__(self,name):return getattr(self.entry,name)
            def stat(self,**options):
                info=self.entry.stat(**options)
                return SimpleNamespace(st_mode=info.st_mode,st_nlink=info.st_nlink,st_file_attributes=0x400)
        class Scan:
            def __init__(self,path):self.scan=native(path)
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
            def __iter__(self):return self
            def __next__(self):
                value=next(self.scan);count[0]+=1;return Reparse(value) if value.name==reparse_name else value
            def close(self):self.scan.close()
        return count,Scan

    def listing(self):return self.files.execute({'tool':'list'},False)

    def test_output_budget_stops_scan_before_all_files(self):
        for index in range(1500):(self.files.folder/(f'{index:04d}-'+'x'*95+'.txt')).touch()
        count,scan=self.counted()
        with patch('app.os.scandir',side_effect=scan):value=self.listing()
        self.assertLess(count[0],500)
        self.assertLessEqual(len(value),20000);self.assertIn('생략',value)

    def test_entry_budget_also_bounds_empty_directory_walks(self):
        for index in range(2100):(self.files.folder/f'd{index:04d}').mkdir()
        count,scan=self.counted()
        with patch('app.os.scandir',side_effect=scan):value=self.listing()
        self.assertLessEqual(count[0],2001);self.assertIn('생략',value)

    def test_hardlinks_are_not_advertised_as_readable_files(self):
        original=self.root/'original';original.write_text('private-fixture')
        (self.files.folder/'outside-alias.txt').hardlink_to(original)
        nested=self.files.folder/'nested';nested.mkdir();(nested/'valid.txt').write_text('valid')
        value=self.listing()
        self.assertNotIn('outside-alias',value);self.assertIn(str(Path('nested')/'valid.txt'),value)
        self.assertEqual(original.read_text(),'private-fixture')

    def test_reparse_directory_metadata_is_not_traversed(self):
        blocked=self.files.folder/'reparse';blocked.mkdir();(blocked/'private-marker.txt').write_text('fixture')
        count,scan=self.counted(reparse_name='reparse')
        with patch('app.os.scandir',side_effect=scan) as scanned:value=self.listing()
        self.assertNotIn('private-marker',value);self.assertEqual(scanned.call_count,1)


if __name__=='__main__':unittest.main()
