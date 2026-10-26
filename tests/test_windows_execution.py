import base64,io,json,subprocess,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import windows_guest as w
import environment_profiles as profiles
from execution_targets import support
from workspace_resources import PROGRAM_BY_ID
from app import Store
from setup_config import write_setting
from vm_runtime import settings,VM,DEFAULT

class WindowsExecutionTests(unittest.TestCase):
 def test_windows_10_lightweight_memory_and_recommended_default(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
   root=Path(folder).resolve();(root/'disk.qcow2').write_bytes(b'fixture');s=Store(root/'data');v=VM(s)
   self.assertEqual(settings(s)['memory_mb'],4096)
   config={**DEFAULT,'enabled':True,'guest_os':'windows','memory_mb':2048,'iso':'','disk':'disk.qcow2','expected_updated':0}
   with patch('vm_runtime.ROOT',root):
    self.assertEqual(v.save(config)['memory_mb'],2048)
    with self.assertRaisesRegex(ValueError,'2048MB'):v.save({**config,'memory_mb':1024})
 def test_windows_boot_allows_installer_reboot_linux_retains_exit_guard(self):
  from types import SimpleNamespace
  for guest in ('windows','linux'):
   with self.subTest(guest=guest),tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
    root=Path(folder).resolve();(root/'runtime/qemu').mkdir(parents=True);(root/'runtime/qemu/qemu-system-x86_64.exe').write_bytes(b'fixture');(root/'boot.iso').write_bytes(b'fixture');(root/'disk.qcow2').write_bytes(b'fixture')
    s=Store(root/'data');write_setting(s,'vm',{**DEFAULT,'enabled':True,'guest_os':guest,'memory_mb':4096,'iso':'boot.iso','disk':'disk.qcow2'})
    v=VM(s)
    with patch('vm_runtime.ROOT',root),patch('vm_runtime.hardware',return_value={'available_ram_gib':64}),patch.object(v,'session',return_value=None),patch.object(v,'_publish_session'),patch.object(v,'qmp',return_value={'status':'running'}),patch('vm_runtime.subprocess.Popen',return_value=SimpleNamespace(pid=777,poll=lambda:None)) as spawn:
     self.assertEqual(v.start()['status'],'running')
    self.assertEqual('-no-reboot' in spawn.call_args.args[0],guest=='linux')
 def test_installation_mode_cannot_execute_work(self):
  v=self.vm();v.session=lambda:{'config':{'installation_only':True}}
  with patch('windows_guest.rpc') as rpc:
   with self.assertRaisesRegex(ValueError,'설치 전용'):w.command(v,'abcdef123456','developer','echo hello')
   rpc.assert_not_called()
 def test_installation_boot_rejects_linux(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
   s=Store(Path(folder).resolve());write_setting(s,'vm',{'guest_os':'linux','iso':'old.iso'})
   with self.assertRaisesRegex(ValueError,'Windows 설치 ISO'):VM(s).start_installation()
 def test_new_defaults_windows_and_legacy_explicit_linux(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
   s=Store(folder);self.assertEqual(settings(s)['guest_os'],'windows');self.assertEqual(profiles.settings(s)['target_os'],'windows')
   write_setting(s,'vm',{'enabled':True,'iso':'old.iso'});self.assertEqual(settings(s)['guest_os'],'linux')
 def test_contract_matches_vm_os_and_never_falls_back(self):
  p={'target_os':'windows','location':'shared_vm','budget_percent':50}
  for vm in ({'enabled':False,'guest_os':'windows'},{'enabled':True,'guest_os':'linux'}):
   with self.assertRaises(profiles.UnavailableEnvironment):profiles.resolve(p,vm=vm)
  c=profiles.resolve(p,vm={'enabled':True,'guest_os':'windows'});self.assertEqual(c['runner'],'qemu');self.assertEqual(profiles.check_contract(c,'vm'),c)
  with self.assertRaises(ValueError):profiles.check_contract(c,'local')
 def test_program_support_is_environment_specific(self):
  self.assertFalse(support(PROGRAM_BY_ID['unity'],'shared_vm','linux')['supported'])
  self.assertFalse(support(PROGRAM_BY_ID['git'],'remote','windows')['supported'])
  self.assertTrue(support(PROGRAM_BY_ID['git'],'shared_vm','windows')['automatic'])
  self.assertFalse(support(PROGRAM_BY_ID['unity'],'shared_vm','windows')['automatic'])
 def vm(self):
  class Fake:
   thread_lock=threading.Lock()
   def guest_active(self,tid):pass
   def session(self):return {'agent_port':1234,'name':'unit','config':{'guest_os':'windows'}}
  v=Fake();v.thread_lock.acquire();return v
 def test_windows_script_output_failure_unicode_and_missing_command(self):
  # Actual Windows PowerShell parser/process; QGA transport is mocked, NOT a VM test.
  v=self.vm()
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
   v.receipt_root=str(Path(folder)/'receipts')
   def rpc(vm,method,args=None):
    if method=='guest-get-osinfo':return {'id':'mswindows'}
    if method=='guest-exec':
     script=base64.b64decode(args['arg'][-1]).decode('utf-16le').replace('C:\\MOA\\tasks\\abcdef123456',str(Path(folder).resolve()))
     result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
     self.last={'exited':True,'exitcode':result.returncode,'out-data':base64.b64encode(result.stdout).decode(),'err-data':base64.b64encode(result.stderr).decode()};return {'pid':1}
    return self.last
   with patch('windows_guest.rpc',side_effect=rpc):
    r=w.command(v,'abcdef123456','developer',"Set-Content -LiteralPath answer.txt -Value '꽃' -Encoding UTF8;Get-Content answer.txt")
    self.assertEqual(r['exit_code'],0);self.assertIn('꽃',r['output'])
    self.assertEqual(w.command(v,'abcdef123456','reviewer',"throw 'failed assertion'")['exit_code'],1)
    self.assertEqual(w.command(v,'abcdef123456','developer','moa_nonexistent_program_12345')['exit_code'],127)
    class Workspace:
     def path(self,name):return Path(folder)/name
    exported=w.export_workspace(v,'abcdef123456','reviewer',Workspace())
    import tarfile
    with tarfile.open(Path(folder)/exported['path']) as tar:self.assertIn('answer.txt',tar.getnames())
 def test_wrong_actual_os_prevents_command_execution(self):
  with patch('windows_guest.rpc',return_value={'id':'linux'}) as rpc:
   with self.assertRaisesRegex(ValueError,'Windows가 아닙니다'):w.command(self.vm(),'abcdef123456','developer','echo hello')
   self.assertEqual(rpc.call_count,1)
 def test_timeout_does_not_replay_guest_exec(self):
  calls=[]
  def rpc(vm,method,args=None):
   calls.append(method)
   return {'id':'mswindows'} if method=='guest-get-osinfo' else {'pid':42} if method=='guest-exec' else {'exited':False}
  with patch('windows_guest.rpc',side_effect=rpc):
   with self.assertRaisesRegex(ValueError,'자동 재실행하지 않습니다'):w.command(self.vm(),'abcdef123456','developer','echo hello',timeout=0)
  self.assertEqual(calls.count('guest-exec'),1)
 def test_wire_protocol_sync_and_stale_reply(self):
  import socket
  errors=[]
  with socket.socket() as server:
   server.bind(('127.0.0.1',0));server.listen(1);port=server.getsockname()[1]
   def respond():
    try:
     conn,_=server.accept()
     with conn,conn.makefile('rb') as stream:
      sync=json.loads(stream.readline().lstrip(b'\xff'))
      conn.sendall((json.dumps({'return':sync['arguments']['id'],'id':sync['id']})+'\n').encode())
      req=json.loads(stream.readline())
      conn.sendall((json.dumps({'return':{},'id':'old-request'})+'\n'+json.dumps({'return':{'id':'mswindows'},'id':req['id']})+'\n').encode())
    except Exception as e:errors.append(e)
   thread=threading.Thread(target=respond,daemon=True);thread.start();vm=self.vm();vm.session=lambda:{'agent_port':port}
   self.assertEqual(w.rpc(vm,'guest-get-osinfo'),{'id':'mswindows'});thread.join(3);self.assertFalse(thread.is_alive());self.assertFalse(errors)
 def test_windows_work_requires_actual_guest_probe(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
   s=Store(folder);write_setting(s,'vm',{**DEFAULT,'enabled':True,'guest_os':'windows'})
   with self.assertRaisesRegex(ValueError,'Windows 설치 후'):
    s.add('Windows에서 파일을 작성해 주세요','llama',environment='profile')

if __name__=='__main__':unittest.main()
