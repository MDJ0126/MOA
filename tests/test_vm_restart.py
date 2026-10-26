import unittest
from contextlib import nullcontext
from unittest.mock import Mock
from vm_runtime import VM

class RestartTests(unittest.TestCase):
 def make(self):
  v=VM.__new__(VM);v.exclusive=Mock(side_effect=lambda:nullcontext());v.session=Mock(return_value={'pid':1});v._shutdown_gracefully=Mock();v._start=Mock(return_value={'status':'running'});return v
 def test_normal_shutdown_before_start(self):
  v=self.make();calls=[];v._shutdown_gracefully.side_effect=lambda **kw:calls.append('shutdown');v._start.side_effect=lambda:calls.append('start') or {'status':'running'}
  self.assertEqual(v.restart(),{'status':'running'});self.assertEqual(calls,['shutdown','start']);v._shutdown_gracefully.assert_called_once_with(timeout=90)
 def test_shutdown_failure_preserves_guest(self):
  v=self.make();v._shutdown_gracefully.side_effect=ValueError('pending')
  with self.assertRaisesRegex(ValueError,'pending'):v.restart()
  v._start.assert_not_called()
 def test_stopped_guest_rejected(self):
  v=self.make();v.session.return_value=None
  with self.assertRaises(ValueError):v.restart()
  v._shutdown_gracefully.assert_not_called();v._start.assert_not_called()
