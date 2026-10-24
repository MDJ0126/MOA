from pathlib import Path
import tempfile
import unittest
from checks import run_checks


class CheckBytecodeTests(unittest.TestCase):
    def test_verification_does_not_create_or_replace_workspace_bytecode(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            root=Path(temporary);workspace=root/'workspace';workspace.mkdir()
            (workspace/'helper.py').write_text('VALUE=7\n',encoding='utf-8')
            (workspace/'test_value.py').write_text('import unittest,helper\nclass Tests(unittest.TestCase):\n def test_value(self):self.assertEqual(helper.VALUE,7)\n',encoding='utf-8')
            result=run_checks(workspace)
            self.assertTrue(result['passed'],result);self.assertEqual(result['tests_run'],1)
            self.assertFalse((workspace/'__pycache__').exists())
            cache=workspace/'__pycache__';cache.mkdir();old=cache/'keep.pyc';old.write_bytes(b'existing-user-cache')
            result=run_checks(workspace)
            self.assertTrue(result['passed'],result)
            self.assertEqual(list(cache.iterdir()),[old]);self.assertEqual(old.read_bytes(),b'existing-user-cache')


if __name__=='__main__':unittest.main()
