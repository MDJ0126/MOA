import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from app import Store
from vm_runtime import VM
from vm_shutdown import finish_vm_shutdown
from setup_config import read_setting
class GracefulShutdownTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup);self.store=Store(self.temp.name);self.vm=VM(self.store);self.record={'name':'owned','pid':123,'port':1};self.vm.session_file.write_text(json.dumps(self.record));self.worker=Mock();self.worker.is_alive.return_value=False
 def test_normal_shutdown_requests_acpi_and_cleans_only_after_exit(self):
  with patch.object(self.vm,'qmp',return_value={'status':'running'}) as qmp,patch('vm_runtime.pid_alive',side_effect=[True,True,False]),patch('vm_runtime.time.sleep'):
   self.assertEqual(self.vm.shutdown_gracefully()['state'],'stopped')
  self.assertEqual([c.args[0] for c in qmp.call_args_list],['query-status','system_powerdown']);self.assertFalse(self.vm.session_file.exists())
 def test_blocked_shutdown_preserves_guest_and_reports_pending(self):
  with patch.object(self.vm,'qmp',return_value={'status':'running'}) as qmp,patch('vm_runtime.pid_alive',return_value=True),patch('vm_runtime.time.monotonic',side_effect=[0,0,91]),patch('vm_shutdown.time.time',return_value=100),patch.object(self.vm,'shutdown_gracefully',wraps=self.vm.shutdown_gracefully):
   result=finish_vm_shutdown(self.store,self.vm,self.worker)
  self.assertEqual(result['state'],'pending');self.assertFalse(result['forced']);self.assertIn('정상 종료',result['error']);self.assertTrue(self.vm.session_file.exists());self.assertNotIn('quit',[c.args[0] for c in qmp.call_args_list]);self.assertEqual(read_setting(self.store,'vm_shutdown_result',{})['state'],'pending')
 def test_no_guest_and_worker_still_active_are_safe(self):
  self.vm.session_file.unlink();self.assertEqual(self.vm.shutdown_gracefully(),{'state':'stopped','requested':False});self.worker.is_alive.return_value=True
  with patch.object(self.vm,'shutdown_gracefully') as stop:result=finish_vm_shutdown(self.store,self.vm,self.worker)
  stop.assert_not_called();self.assertEqual(result['state'],'pending')
 def test_paused_guest_resumes_to_process_normal_shutdown(self):
  with patch.object(self.vm,'qmp',return_value={'status':'paused'}) as qmp,patch('vm_runtime.pid_alive',side_effect=[True,False]):self.vm.shutdown_gracefully()
  self.assertEqual([c.args[0] for c in qmp.call_args_list],['query-status','cont','system_powerdown'])
 def test_windows_uses_normal_guest_shutdown_without_force(self):
  session={**self.record,'config':{'guest_os':'windows'}};self.vm.session_file.write_text(json.dumps(session))
  with patch.object(self.vm,'qmp',return_value={'status':'running'}) as qmp,patch('vm_runtime.pid_alive',side_effect=[True,False]),patch('windows_guest.rpc',side_effect=[{'id':'mswindows'},{'pid':8}]) as rpc:
   result=self.vm.shutdown_gracefully()
  self.assertEqual(result['method'],'guest_windows_shutdown');self.assertEqual(rpc.call_args.args[2]['arg'],['/s','/t','0']);self.assertNotIn('quit',[c.args[0] for c in qmp.call_args_list])
 def test_changed_session_is_preserved(self):
  with patch.object(self.vm,'session',side_effect=[self.record,{**self.record,'name':'new-boot'}]),patch('vm_runtime.pid_alive',return_value=False):
   with self.assertRaisesRegex(ValueError,'변경'):self.vm.shutdown_gracefully()
  self.assertTrue(self.vm.session_file.exists())
