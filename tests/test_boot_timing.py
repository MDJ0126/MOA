import tempfile,unittest
from pathlib import Path
from app import Store
from boot_timing import observe
class BootTimingTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup);self.store=Store(self.temp.name);self.session={'name':'boot-a','pid':1,'started':100,'config':{'guest_os':'windows','memory_mb':2048,'cpus':2,'accelerator':'tcg','disk':'disk.qcow2'}}
 def test_success_persists_once_and_next_boot_counts_down_then_delays(self):
  a=observe(self.store,self.session,'running',{'state':'waiting'},now=110);self.assertIsNone(a['estimated_seconds']);self.assertEqual(a['elapsed_seconds'],10)
  a=observe(self.store,self.session,'running',{'state':'ready','checked_at':140},now=141);self.assertEqual(a['last_success_seconds'],40)
  a=observe(self.store,self.session,'running',{'state':'ready','checked_at':180},now=181);self.assertEqual(a['last_success_seconds'],40)
  new={**self.session,'name':'boot-b','started':200};a=observe(Store(self.temp.name),new,'running',{'state':'waiting'},now=210);self.assertEqual(a['remaining_seconds'],30);self.assertFalse(a['delayed'])
  a=observe(self.store,new,'running',{'state':'waiting'},now=245);self.assertTrue(a['delayed']);self.assertEqual(a['remaining_seconds'],0);self.assertEqual(len(a['history']),2)
 def test_failed_boot_and_different_configuration_do_not_create_fake_estimates(self):
  observe(self.store,self.session,'running',{'state':'waiting'},now=110);a=observe(self.store,self.session,'stopped',{'state':'waiting'},now=120);self.assertEqual(a['history'][0]['state'],'interrupted');self.assertIsNone(a['last_success_seconds'])
  new={**self.session,'name':'boot-b','started':200};observe(self.store,new,'running',{'state':'ready'},now=230)
  changed={**new,'name':'boot-c','started':300,'config':{**new['config'],'memory_mb':4096}};a=observe(self.store,changed,'running',{'state':'waiting'},now=310);self.assertIsNone(a['estimated_seconds'])
 def test_missing_history_never_fabricates_time_and_stall_is_explicit(self):
  a=observe(self.store,self.session,'running',{'state':'waiting'},now=401);self.assertTrue(a['delayed']);self.assertIsNone(a['remaining_seconds']);self.assertFalse(observe(self.store,{},'stopped',{},now=500)['active'])

 def test_average_uses_only_successes_with_same_configuration(self):
  observe(self.store,self.session,'running',{'state':'ready','checked_at':120},now=121)
  other={**self.session,'name':'boot-b','started':200};a=observe(self.store,other,'running',{'state':'ready','checked_at':240},now=241)
  self.assertEqual(a['last_success_seconds'],40);self.assertEqual(a['average_success_seconds'],30);self.assertEqual(a['successful_boot_count'],2)
  failed={**other,'name':'boot-failed','started':300};observe(self.store,failed,'running',{},now=310);a=observe(self.store,failed,'stopped',{},now=320)
  self.assertEqual(a['average_success_seconds'],30)
