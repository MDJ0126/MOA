import unittest
from unittest.mock import patch
from test_runtime import node_executable


class NodeSelectionTests(unittest.TestCase):
    def test_explicit_path_wins(self):
        with patch.dict('os.environ', {'MOA_NODE': 'custom-node.exe'}), patch('test_runtime.Path.is_file', return_value=True):
            self.assertEqual(node_executable(), 'custom-node.exe')

    def test_bad_override_is_not_silently_ignored(self):
        with patch.dict('os.environ', {'MOA_NODE': 'missing.exe'}), patch('test_runtime.Path.is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'MOA_NODE'):node_executable()

    def test_normal_installation_without_codex(self):
        with patch.dict('os.environ', {}, clear=True), patch('test_runtime.shutil.which', return_value='ordinary-node.exe'):
            self.assertEqual(node_executable(), 'ordinary-node.exe')
