from pathlib import Path
import tempfile
import unittest
from diagnostics import inspect_installation


class DiagnosticsTests(unittest.TestCase):
    def test_missing_optional_and_ready_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as folder:
            result = inspect_installation(folder, {})
            states = {item['id']: item['state'] for item in result['checks']}
            self.assertEqual(states['model_file'], 'missing')
            self.assertEqual(states['slack_sdk'], 'optional')
            self.assertEqual(states['vm_runtime'], 'optional')
            (Path(folder) / 'model.gguf').write_bytes(b'test')
            result = inspect_installation(folder, {'model_file': 'model.gguf'}, True)
            states = {item['id']: item['state'] for item in result['checks']}
            self.assertEqual(states['model_file'], 'ready')
            self.assertEqual(states['slack_sdk'], 'ready')

    def test_external_and_invalid_paths_do_not_report_ready(self):
        result = inspect_installation(Path(__file__).parent.parent, {'model_file': '../outside', 'runtime_executable': []})
        states = {item['id']: item['state'] for item in result['checks']}
        self.assertEqual(states['model_file'], 'error')
        self.assertEqual(states['runtime_executable'], 'missing')
