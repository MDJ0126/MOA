from contextlib import redirect_stdout
import io
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from verify_clean_install import run_units


class CleanInstallDiagnosticsTests(unittest.TestCase):
    def test_real_python_suite_writes_and_reads_summary(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            (Path(temporary)/'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_value(self):self.assertEqual(2+2,4)\n',encoding='utf-8')
            output=io.StringIO()
            with redirect_stdout(output):self.assertEqual(run_units(temporary,timeout=10),0)
            self.assertIn('Ran 1 test',output.getvalue());self.assertIn('OK',output.getvalue())
            (Path(temporary)/'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_value(self):self.fail("진단 오류 확인")\n',encoding='utf-8')
            output=io.StringIO()
            with redirect_stdout(output):self.assertEqual(run_units(temporary,timeout=10),1)
            self.assertIn('진단 오류 확인',output.getvalue())

    def test_timeout_reports_last_started_test_and_failure_code(self):
        def timeout(command,**options):
            options['stdout'].write(b'test_slow_fixture ... ')
            raise subprocess.TimeoutExpired(command,options['timeout'])
        output=io.StringIO()
        with patch('verify_clean_install.subprocess.run',side_effect=timeout),redirect_stdout(output):
            self.assertEqual(run_units('fixture',timeout=7),124)
        self.assertIn('test_slow_fixture',output.getvalue());self.assertIn('7',output.getvalue())

    def test_failure_output_is_bounded_and_keeps_last_error(self):
        def fail(command,**options):
            options['stdout'].write(b'x'*100000+b'\nLAST_ERROR_MARKER\n')
            return SimpleNamespace(returncode=2)
        output=io.StringIO()
        with patch('verify_clean_install.subprocess.run',side_effect=fail),redirect_stdout(output):
            self.assertEqual(run_units('fixture'),2)
        self.assertIn('LAST_ERROR_MARKER',output.getvalue());self.assertLess(len(output.getvalue()),9000)

    def test_success_prints_summary_instead_of_full_test_list(self):
        def success(command,**options):
            options['stdout'].write(b'test_private_fixture ... ok\n' * 100+b'----\nRan 100 tests\n\nOK\n')
            return SimpleNamespace(returncode=0)
        output=io.StringIO()
        with patch('verify_clean_install.subprocess.run',side_effect=success),redirect_stdout(output):
            self.assertEqual(run_units('fixture'),0)
        self.assertIn('Ran 100 tests',output.getvalue());self.assertNotIn('test_private_fixture',output.getvalue())


if __name__=='__main__':unittest.main()
