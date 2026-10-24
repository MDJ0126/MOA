import ctypes
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app import Store
from setup_config import write_setting
from vm_runtime import VM,DEFAULT,whpx_capability


class WHPXTests(unittest.TestCase):
    def test_native_capability_protocol_and_unknown_results(self):
        for present,result,written,expected in ((1,0,4,True),(0,0,4,False),(1,-1,4,None),(1,0,0,None)):
            with self.subTest(present=present,result=result,written=written):
                def query(code,buffer,size,output_size):
                    self.assertEqual(code,0);self.assertEqual(size,4)
                    ctypes.cast(buffer,ctypes.POINTER(ctypes.c_int))[0]=present
                    ctypes.cast(output_size,ctypes.POINTER(ctypes.c_uint))[0]=written
                    return result
                with patch('vm_runtime.os.name','nt'),patch('vm_runtime.ctypes.WinDLL',return_value=SimpleNamespace(WHvGetCapability=query),create=True) as loader:
                    self.assertIs(whpx_capability()['available'],expected)
                    loader.assert_called_once_with('WinHvPlatform.dll',winmode=0x800)

    def test_missing_api_does_not_claim_readiness(self):
        with patch('vm_runtime.os.name','nt'),patch('vm_runtime.ctypes.WinDLL',side_effect=OSError('missing'),create=True):self.assertIs(whpx_capability()['available'],False)

    def test_non_windows_does_not_load_windows_library(self):
        with patch('vm_runtime.os.name','posix'),patch('vm_runtime.ctypes.WinDLL',create=True) as loader:
            self.assertIs(whpx_capability()['available'],False);loader.assert_not_called()

    def test_unavailable_or_unknown_acceleration_cannot_spawn_vm(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            store=Store(temporary);vm=VM(store);write_setting(store,'vm',dict(DEFAULT,enabled=True,accelerator='whpx'))
            for available in (False,None):
                with self.subTest(available=available),patch('vm_runtime.whpx_capability',return_value={'available':available,'reason':'test unavailable'}),patch('vm_runtime.subprocess.Popen') as spawn:
                    with self.assertRaisesRegex(ValueError,'TCG'):vm.start()
                    spawn.assert_not_called();self.assertFalse(vm.session_file.exists())

    def test_existing_vm_status_does_not_probe_or_spawn_again(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            store=Store(temporary);vm=VM(store);write_setting(store,'vm',dict(DEFAULT,enabled=True,accelerator='whpx'))
            with patch.object(vm,'session',return_value={'pid':123,'port':1234,'name':'moa-owned','config':dict(DEFAULT,enabled=True,accelerator='whpx')}),patch.object(vm,'qmp',return_value={'status':'running'}),patch('vm_runtime.whpx_capability') as probe,patch('vm_runtime.subprocess.Popen') as spawn:
                self.assertEqual(vm.start(),{'status':'running'});probe.assert_not_called();spawn.assert_not_called()
