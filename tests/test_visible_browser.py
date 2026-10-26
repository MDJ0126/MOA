import unittest,sys
from unittest.mock import patch
sys.path.insert(0,'src')
from vm_runtime import VM
import visible_browser
class ActivityTests(unittest.TestCase):
 def test_nested_failure_restores_mode_and_clears(self):
  values={};v=object.__new__(VM);v.store=object();v.session=lambda:{'name':'test','config':{'guest_os':'windows'}}
  with patch('vm_runtime.read_setting',side_effect=lambda s,k,d=None:values.get(k,d)),patch('vm_runtime.write_setting',side_effect=lambda s,k,x:values.update({k:x})):
   with v.activity('browser','검색','abc','developer'):
    with self.assertRaises(RuntimeError):
     with v.activity('cli','명령','abc','developer'):
      self.assertEqual(values['vm_activity']['mode'],'cli');raise RuntimeError()
    self.assertEqual(values['vm_activity']['mode'],'browser')
   self.assertIsNone(values['vm_activity'])
 def test_browser_failure_does_not_silently_use_host_fetch(self):
  with patch.object(visible_browser,'ensure',side_effect=ValueError('브라우저 오류')),patch('web_research.image_search') as host:
   with self.assertRaisesRegex(ValueError,'브라우저 오류'):visible_browser.execute(None,'search_images','flowers',None,lambda *a:None)
   host.assert_not_called()
if __name__=='__main__':unittest.main()
