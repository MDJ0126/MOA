import ctypes
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from data_backup import publish_restored

class RestorePublishTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'staged';self.source.mkdir();self.target=self.root/'restored'
        (self.source/'state.sqlite3').write_bytes(b'fixture only')
    def error(self,code):
        error=PermissionError('simulated Windows publish failure');error.winerror=code;return error
    @unittest.skipUnless(os.name=='nt','native Windows handle semantics')
    def test_native_child_handle_reproduces_winerror_5_then_recovers(self):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p];kernel.CreateFileW.restype=ctypes.c_void_p
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.CreateFileW(str(self.source/'state.sqlite3'),0x80000000,3,None,3,0x80,None)
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        try:
            with self.assertRaises(OSError) as caught:self.source.rename(self.target)
            self.assertEqual(caught.exception.winerror,5)
            def release(delay):
                nonlocal handle
                kernel.CloseHandle(handle);handle=None
            with patch('data_backup.time.sleep',side_effect=release) as sleep:publish_restored(self.source,self.target)
            sleep.assert_called_once();self.assertEqual((self.target/'state.sqlite3').read_bytes(),b'fixture only')
        finally:
            if handle:kernel.CloseHandle(handle)
    def test_exhausted_denial_is_bounded_and_preserves_source(self):
        error=self.error(5)
        with patch('data_backup.os',SimpleNamespace(name='nt',path=os.path)),patch.object(Path,'rename',side_effect=error) as rename,patch('data_backup.time.sleep') as sleep:
            with self.assertRaises(PermissionError) as caught:publish_restored(self.source,self.target)
        self.assertIs(caught.exception,error);self.assertEqual(rename.call_count,4);self.assertEqual(sleep.call_count,3)
        self.assertTrue(self.source.is_dir());self.assertFalse(self.target.exists())
    def test_new_destination_during_retry_is_never_replaced(self):
        with patch('data_backup.os',SimpleNamespace(name='nt',path=os.path)),patch.object(Path,'rename',side_effect=self.error(32)) as rename,patch('data_backup.time.sleep',side_effect=lambda delay:self.target.mkdir()):
            with self.assertRaisesRegex(ValueError,'이미 생성'):publish_restored(self.source,self.target)
        self.assertEqual(rename.call_count,1);self.assertTrue(self.source.is_dir());self.assertTrue(self.target.is_dir())
    def test_unrelated_error_is_not_retried(self):
        error=self.error(87)
        with patch.object(Path,'rename',side_effect=error) as rename,patch('data_backup.time.sleep') as sleep:
            with self.assertRaises(PermissionError):publish_restored(self.source,self.target)
        self.assertEqual(rename.call_count,1);sleep.assert_not_called()
    def test_existing_file_is_preserved(self):
        self.target.write_text('keep',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'이미 생성'):publish_restored(self.source,self.target)
        self.assertEqual(self.target.read_text(encoding='utf-8'),'keep')
    def test_nonwindows_does_not_retry_windows_number(self):
        with patch('data_backup.os',SimpleNamespace(name='posix',path=os.path)),patch.object(Path,'rename',side_effect=self.error(5)) as rename,patch('data_backup.time.sleep') as sleep:
            with self.assertRaises(PermissionError):publish_restored(self.source,self.target)
        self.assertEqual(rename.call_count,1);sleep.assert_not_called()

if __name__=='__main__':unittest.main()
