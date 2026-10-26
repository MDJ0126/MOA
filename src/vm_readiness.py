"""Verify guest desktop and command execution without blocking state polling."""
import base64,threading,time
from setup_config import write_setting
import windows_guest

def check_windows(vm):
 def rpc(execute,arguments=None,timeout=2):
  return windows_guest.rpc(vm,execute,arguments,timeout=timeout)
 info=rpc('guest-get-osinfo',timeout=2)
 if info.get('id')!='mswindows':raise ValueError('Windows 운영체제 확인 대기')
 script="if(Get-Process explorer -ErrorAction SilentlyContinue){Write-Output 'MOA_DESKTOP_READY';exit 0};exit 2"
 pid=rpc('guest-exec',{'path':r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','arg':['-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],'capture-output':True},timeout=2)['pid']
 deadline=time.monotonic()+25
 while time.monotonic()<deadline:
  result=rpc('guest-exec-status',{'pid':pid},timeout=2)
  if result.get('exited'):
   if result.get('exitcode')!=0 or b'MOA_DESKTOP_READY' not in base64.b64decode(result.get('out-data','')):raise ValueError('Windows 로그인·바탕화면 준비 대기')
   return info
  time.sleep(.25)
 raise ValueError('Windows 작업 명령 응답 대기')

class GuestReadiness:
 def __init__(self,vm):self.vm=vm;self.lock=threading.Lock();self.key=None;self.value={'state':'waiting','reason':'Windows 준비 확인 중'};self.checking=False;self.checked=0
 def snapshot(self,state,session):
  key=(session.get('name'),session.get('pid')) if session else None
  with self.lock:
   if key!=self.key:self.key=key;self.value={'state':'waiting','reason':'Windows 부팅·작업 연결 확인 중'};self.checked=0
   if state!='running':
    self.value={'state':'waiting','reason':'VM 전원 대기'};self.checked=0
    return dict(self.value)
   if session.get('config',{}).get('installation_only'):return {'state':'waiting','reason':'Windows 설치 대기'}
   if session.get('config',{}).get('guest_os')!='windows':return {'state':'waiting','reason':'작업 환경 준비 확인 필요'}
   if not self.checking and time.monotonic()-self.checked>=(30 if self.value['state']=='ready' else 5):
    self.checking=True;threading.Thread(target=self.check,args=(key,),daemon=True).start()
   return dict(self.value)
 def check(self,key):
  value=None
  try:
   info=check_windows(self.vm)
   if (self.vm.session().get('name'),self.vm.session().get('pid'))!=key:return
   value={'state':'ready','reason':'Windows 바탕화면·작업 명령 확인 완료','checked_at':time.time()}
   write_setting(self.vm.store,'windows_guest_probe',{'session':key[0],'os':info,'desktop_ready':True,'checked_at':value['checked_at']})
   try:
    from boot_timing import observe
    observe(self.vm.store,self.vm.session(),'running',value)
   except Exception as error:value['timing_error']=str(error)[:200]
  except Exception as error:
   from vm_runtime import BusyVM
   if not isinstance(error,(BusyVM,windows_guest.GuestAgentBusy)):value={'state':'waiting','reason':'Windows 바탕화면·작업 연결 대기','error':str(error)[:240],'checked_at':time.time()}
  finally:
   with self.lock:
    if self.key==key:
     if value is not None:self.value=value
     self.checked=time.monotonic()
    self.checking=False
