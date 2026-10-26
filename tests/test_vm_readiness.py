import unittest,base64
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from vm_readiness import GuestReadiness,check_windows
class ReadinessTests(unittest.TestCase):
 def setUp(self):self.session={'name':'boot-a','pid':123,'config':{'guest_os':'windows'}};self.vm=SimpleNamespace(session=lambda:self.session,exclusive=lambda:nullcontext(),store=object());self.monitor=GuestReadiness(self.vm)
 def test_power_on_is_not_readiness_and_new_boot_forgets_old_ready(self):
  with patch('vm_readiness.threading.Thread'):
   self.assertEqual(self.monitor.snapshot('running',self.session)['state'],'waiting')
   with patch('vm_readiness.check_windows',return_value={'id':'mswindows'}),patch('vm_readiness.write_setting') as save:self.monitor.check(('boot-a',123));self.assertTrue(save.call_args.args[2]['desktop_ready'])
   self.assertEqual(self.monitor.snapshot('running',self.session)['state'],'ready');self.session={**self.session,'name':'boot-b'};self.assertEqual(self.monitor.snapshot('running',self.session)['state'],'waiting')
 def test_failed_connection_never_activates(self):
  with patch('vm_readiness.threading.Thread'):self.monitor.snapshot('running',self.session)
  with patch('vm_readiness.check_windows',side_effect=ValueError('agent unavailable')):self.monitor.check(('boot-a',123))
  self.assertEqual(self.monitor.value['state'],'waiting');self.assertIn('agent unavailable',self.monitor.value['error'])
 def test_desktop_and_actual_powershell_output_required(self):
  for code,output in ((2,b''),(0,b'wrong'),(0,b'MOA_DESKTOP_READY\r\n')):
   with patch('vm_readiness.windows_guest.rpc',side_effect=[{'id':'mswindows'},{'pid':42},{'exited':True,'exitcode':code,'out-data':base64.b64encode(output).decode()}]) as rpc:
    if code==0 and b'MOA_DESKTOP_READY' in output:self.assertEqual(check_windows(self.vm)['id'],'mswindows')
    else:
     with self.assertRaises(ValueError):check_windows(self.vm)
    args=rpc.call_args_list[1].args[2]['arg'];self.assertIn('Get-Process explorer',base64.b64decode(args[-1]).decode('utf-16le'))
