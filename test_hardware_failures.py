import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
from setup_config import hardware,cpu_usage
from soak import freeze
from app_lifecycle import request_stop


class HardwareFailureTests(unittest.TestCase):
    def windows_probe(self,name='CPU fixture',memory_error=None):
        def memory(pointer):
            if memory_error:raise memory_error
            pointer._obj.total=16*2**30;pointer._obj.available=8*2**30;return 1
        registry=SimpleNamespace(HKEY_LOCAL_MACHINE=1,OpenKey=MagicMock(),QueryValueEx=MagicMock(return_value=(name,1)))
        native=SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=memory))
        with patch('setup_config.platform.system',return_value='Windows'),patch('setup_config.ctypes.windll',native,create=True),patch('setup_config.cpu_usage',return_value=None),patch.dict('sys.modules',winreg=registry):return hardware()

    def test_optional_windows_memory_failure_leaves_setup_available(self):
        value=self.windows_probe(memory_error=OSError('probe unavailable'))
        self.assertIsNone(value['ram_gib']);self.assertIsNone(value['available_ram_gib']);self.assertIsNone(value['memory_used_percent'])
        self.assertEqual(value['cpu'],'CPU fixture')

    def test_non_string_processor_name_does_not_break_setup(self):
        for name in (None,123,[],b'cpu'):
            with self.subTest(value=repr(name)):
                value=self.windows_probe(name=name)
                self.assertNotIn('cpu',value);self.assertEqual(value['ram_gib'],16);self.assertEqual(value['available_ram_gib'],8)

    def test_invalid_memory_is_unknown_instead_of_negative_or_clamped(self):
        for text,total,available in [('MemTotal: -1024 kB\nMemAvailable: 512 kB\n',None,None),('MemTotal: 0 kB\nMemAvailable: 0 kB\n',None,None),('MemTotal: 16777216 kB\nMemAvailable: -1024 kB\n',16,None),('MemTotal: 16777216 kB\nMemAvailable: 33554432 kB\n',16,None)]:
            with self.subTest(text=text),patch('setup_config.platform.system',return_value='Linux'),patch.object(Path,'read_text',return_value=text),patch('setup_config.cpu_usage',return_value=None):
                value=hardware();self.assertEqual(value['ram_gib'],total);self.assertEqual(value['available_ram_gib'],available);self.assertIsNone(value['memory_used_percent'])

    def test_negative_cpu_counter_is_not_reported_as_measured_usage(self):
        with patch('setup_config._cpu_sample',None),patch.object(Path,'read_text',side_effect=['cpu 10 0 0 90 0 0 0 0\n','cpu -1 0 0 110 0 0 0 0\n']):
            self.assertIsNone(cpu_usage('Linux'));self.assertIsNone(cpu_usage('Linux'))

    def test_impossible_cpu_delta_is_unknown_and_next_sample_recovers(self):
        with patch('setup_config._cpu_sample',None),patch.object(Path,'read_text',side_effect=['cpu 10 0 0 90 0 0 0 0\n','cpu 0 0 0 110 0 0 0 0\n','cpu 10 0 0 120 0 0 0 0\n']):
            self.assertIsNone(cpu_usage('Linux'));self.assertIsNone(cpu_usage('Linux'));self.assertEqual(cpu_usage('Linux'),50)

    def test_measured_zero_available_memory_is_preserved(self):
        with patch('setup_config.platform.system',return_value='Linux'),patch.object(Path,'read_text',return_value='MemTotal: 16777216 kB\nMemAvailable: 0 kB\n'),patch('setup_config.cpu_usage',return_value=0):
            value=hardware();self.assertEqual(value['available_ram_gib'],0);self.assertEqual(value['memory_used_percent'],100)


@unittest.skipUnless(os.name=='nt','Windows native probe HTTP boundary')
class HardwareFailureHttpTests(unittest.TestCase):
    def test_setup_stays_available_and_auto_vm_is_held_with_missing_measurements(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root=Path(temporary);project=root/'source';freeze(project)
            source=project/'setup_config.py'
            source.write_text(source.read_text(encoding='utf-8')+'''
_fixture_hardware=hardware
def hardware():
    from unittest.mock import patch
    with patch('setup_config.ctypes.windll.kernel32.GlobalMemoryStatusEx',side_effect=OSError('fixture measurement unavailable')),patch('winreg.QueryValueEx',return_value=(123,4)):
        return _fixture_hardware()
''',encoding='utf-8')
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            def request(path):
                with urllib.request.urlopen(f'http://127.0.0.1:{port}'+path,timeout=3) as response:return json.load(response)
            with (root/'app.log').open('wb') as log:
                child=subprocess.Popen([sys.executable,str(project/'app.py'),'--data',str(root/'data'),'--port',str(port)],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:
                    for _ in range(100):
                        try:request('/api/health');break
                        except OSError:
                            if child.poll() is not None:self.fail('isolated app exited')
                            time.sleep(.05)
                    else:self.fail('isolated app startup timeout')
                    setup=request('/api/setup');self.assertTrue(setup['team']['agents'])
                    self.assertIsNone(setup['hardware']['ram_gib']);self.assertIsNone(setup['hardware']['available_ram_gib']);self.assertNotIn('cpu',setup['hardware'])
                    self.assertIsNone(setup['environment_recommendation']['recommended']);self.assertFalse(setup['environment_recommendation']['connected'])
                    self.assertEqual(request('/api/health')['app']['pid'],child.pid)
                    self.assertEqual(request('/api/state')['tasks'],[])
                finally:
                    if child.poll() is None:
                        try:request_stop(project,port,expected_pid=child.pid);child.wait(timeout=10)
                        except Exception:child.terminate();child.wait(timeout=10)
