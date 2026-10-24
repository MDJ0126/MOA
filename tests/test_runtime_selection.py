import unittest
import subprocess
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from tests.test_runtime import node_executable


class NodeSelectionTests(unittest.TestCase):
    def setUp(self):
        probe=patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'v24.19.0\n',''))
        self.probe=probe.start();self.addCleanup(probe.stop)
    def test_explicit_path_wins(self):
        with patch.dict('os.environ', {'MOA_NODE': 'custom-node.exe'}), patch('tests.test_runtime.Path.is_file', return_value=True):
            self.assertEqual(node_executable(), str(Path('custom-node.exe').resolve()))

    def test_relative_override_remains_valid_from_another_working_directory(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            root=Path(temporary);binary=root/'tools/node.exe';binary.parent.mkdir();binary.write_bytes(b'fixture only; version probe is mocked')
            relative=os.path.relpath(binary,Path.cwd())
            with patch.dict('os.environ',{'MOA_NODE':relative}):selected=Path(node_executable())
            self.assertTrue(selected.is_absolute())
            self.assertEqual((root/'another-working-directory'/selected).resolve(),binary.resolve())

    def test_bad_override_is_not_silently_ignored(self):
        with patch.dict('os.environ', {'MOA_NODE': 'missing.exe'}), patch('tests.test_runtime.Path.is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'MOA_NODE'):node_executable()

    def test_normal_installation_without_codex(self):
        with patch.dict('os.environ', {}, clear=True), patch('tests.test_runtime.shutil.which', return_value='ordinary-node.exe'),patch('tests.test_runtime.Path.is_file',return_value=True):
            self.assertEqual(node_executable(), str(Path('ordinary-node.exe').resolve()))

    def test_incompatible_explicit_node_is_not_silently_accepted(self):
        for version in ('v18.20.0','v20.18.9','v21.7.0','v22.11.0','v23.0.0','v24.0.0-rc.1','not a node version'):
            with self.subTest(version=version),patch.dict('os.environ',{'MOA_NODE':'custom-node.exe'}),patch('tests.test_runtime.Path.is_file',return_value=True):
                self.probe.return_value=subprocess.CompletedProcess([],0,version,'')
                with self.assertRaisesRegex(RuntimeError,'MOA_NODE'):node_executable()

    def test_supported_pinned_jsdom_engine_ranges(self):
        for version in ('v20.19.0','v20.99.0','v22.12.0','v22.99.0','v24.0.0','v24.19.0','v25.1.0'):
            with self.subTest(version=version),patch.dict('os.environ',{'MOA_NODE':'custom-node.exe'}),patch('tests.test_runtime.Path.is_file',return_value=True):
                self.probe.return_value=subprocess.CompletedProcess([],0,version,'')
                self.assertEqual(node_executable(),str(Path('custom-node.exe').resolve()))

    def test_failed_explicit_probe_has_actionable_error(self):
        for error in (OSError('cannot run'),subprocess.TimeoutExpired('node',5)):
            with self.subTest(error=type(error).__name__),patch.dict('os.environ',{'MOA_NODE':'custom-node.exe'}),patch('tests.test_runtime.Path.is_file',return_value=True):
                self.probe.side_effect=error
                with self.assertRaisesRegex(RuntimeError,'MOA_NODE'):node_executable()

    def test_invalid_path_node_can_fall_back_to_existing_compatible_bundle(self):
        self.probe.side_effect=[subprocess.CompletedProcess([],0,'v18.20.0',''),subprocess.CompletedProcess([],0,'v24.19.0','')]
        with patch.dict('os.environ',{},clear=True),patch('tests.test_runtime.shutil.which',return_value='old-node.exe'),patch('tests.test_runtime.Path.is_file',return_value=True),patch('tests.test_runtime.Path.home',return_value=Path('fixture-home')):
            self.assertEqual(node_executable(),str((Path('fixture-home')/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe').resolve()))

    def test_unknown_home_has_actionable_guidance(self):
        self.probe.return_value=subprocess.CompletedProcess([],0,'v18.20.0','')
        with patch.dict('os.environ',{},clear=True),patch('tests.test_runtime.shutil.which',return_value='old-node.exe'),patch('tests.test_runtime.Path.is_file',return_value=True),patch('tests.test_runtime.Path.home',side_effect=RuntimeError('home unavailable')):
            with self.assertRaisesRegex(RuntimeError,'MOA_NODE'):node_executable()
