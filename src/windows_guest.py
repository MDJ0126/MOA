"""Windows QEMU Guest Agent bridge. Never executes guest commands on the host."""
import base64,io,json,re,socket,tarfile,time,uuid,threading,hashlib

_RPC_LOCK=threading.RLock()
class GuestAgentBusy(ValueError):pass

def rpc(vm,execute,arguments=None,timeout=12):
 if not _RPC_LOCK.acquire(timeout=timeout):raise GuestAgentBusy('Windows 명령 연결 사용 중입니다.')
 try:return _rpc(vm,execute,arguments,timeout)
 finally:_RPC_LOCK.release()

def _rpc(vm,execute,arguments=None,timeout=12):
 session=vm.session();port=session.get('agent_port')
 if type(port) is not int or not 1<=port<=65535:raise ValueError('Windows VM을 새로 시작하고 QEMU Guest Agent를 설치하세요.')
 token=uuid.uuid4().hex
 try:
  with socket.create_connection(('127.0.0.1',port),timeout=min(8,timeout)) as sock:
   sock.settimeout(timeout)
   stream=sock.makefile('rb')
   sync=uuid.uuid4().int%(2**63)
   sock.sendall(b'\xff'+(json.dumps({'execute':'guest-sync','arguments':{'id':sync},'id':token+'sync'})+'\n').encode())
   for _ in range(20):
    raw=stream.readline(1024*1024)
    if not raw:raise ValueError('Windows 명령 채널 동기화 실패')
    try:value=json.loads(raw.lstrip(b'\xff'))
    except ValueError:continue
    if value.get('return')==sync:break
   else:raise ValueError('Windows 명령 채널 동기화 응답 미확인')
   sock.sendall((json.dumps({'execute':execute,'arguments':arguments or {},'id':token})+'\n').encode())
   for _ in range(20):
    raw=stream.readline(48*1024*1024)
    if not raw:break
    value=json.loads(raw)
    if value.get('id')!=token:continue
    if 'error' in value:
     description=str(value['error'].get('desc','요청 실패'))
     if execute=='guest-exec-status' and any(x in description.lower() for x in ('not found','not exist','invalid pid')):raise ConnectionError(description)
     raise ValueError('Windows 게스트 연결 오류: '+description[:240])
    return value['return']
 except OSError as error:
  from vm_runtime import VMRecoveryPending
  raise VMRecoveryPending('Windows 연결이 중단되었습니다. 재부팅 후 작업 상태를 확인하여 자동 재개합니다. '+str(error)[:120]) from None
 except (ValueError,KeyError) as error:raise ValueError('Windows VM 응답 미확인. Windows 설치·QEMU Guest Agent 서비스 상태를 확인하세요. '+str(error)[:240]) from None
 raise ValueError('Windows VM의 유효한 응답을 확인하지 못했습니다.')

def receipt_script(workspace,receipt,source64,replay):
 return ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
  +f"$receipt='{receipt}';$replay={replay};"
  +"if($replay -and (Test-Path -LiteralPath $receipt)){ try{$r=Get-Content -LiteralPath $receipt -Raw|ConvertFrom-Json}catch{$r=@{state='uncertain'}};if($r.state -eq 'completed'){Write-Output $r.output;exit ([int]$r.exit_code)};Write-Output 'MOA_RECOVERY_UNCERTAIN: Previous command was interrupted. Inspect actual files/state before choosing a repair; do not blindly repeat side effects.';exit 85};"
  +f"New-Item -ItemType Directory -Force -Path '{workspace}'|Out-Null;Set-Location -LiteralPath '{workspace}';"
  +"New-Item -ItemType Directory -Force -Path (Split-Path $receipt)|Out-Null;@{state='started'}|ConvertTo-Json|Set-Content -LiteralPath $receipt -Encoding UTF8;"
  +f"$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{source64}'));"
  +"$global:LASTEXITCODE=0;$code=0;$text='';try{$text=(& ([ScriptBlock]::Create($source)) 2>&1|Out-String);if($LASTEXITCODE -ne 0){$code=$LASTEXITCODE}}catch{$text=$_.ToString();$code=1;if($_.Exception -is [System.Management.Automation.CommandNotFoundException]){$code=127}};"
  +"@{state='completed';exit_code=$code;output=$text}|ConvertTo-Json -Compress|Set-Content -LiteralPath $receipt -Encoding UTF8;Write-Output $text;exit $code")

def command(vm,tid,role,source,timeout=120,output_limit=12000):
 if (vm.session() or {}).get('config',{}).get('installation_only'):raise ValueError('설치 전용 Windows VM에서는 업무를 실행할 수 없습니다. 업무용 설정으로 재시작하세요.')
 if not vm.thread_lock.locked():raise ValueError('VM 사용권 없이 명령을 실행할 수 없습니다.')
 vm.guest_active(tid)
 info=rpc(vm,'guest-get-osinfo')
 if info.get('id')!='mswindows' and 'windows' not in str(info.get('name','')).lower():raise ValueError('실제 게스트 OS가 Windows가 아닙니다. 다른 OS로 대신 실행하지 않습니다.')
 if not re.fullmatch('[a-f0-9]{12}',tid) or role not in ('planner','developer','reviewer','reporter'):raise ValueError('VM 작업·역할 오류')
 if not isinstance(source,str) or not 1<=len(source)<=12000 or '\x00' in source:raise ValueError('Windows PowerShell 명령은 1~12000자입니다.')
 workspace='C:\\MOA\\tasks\\'+tid
 script="$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"+f"New-Item -ItemType Directory -Force -Path '{workspace}'|Out-Null;Set-Location -LiteralPath '{workspace}';"+"$global:LASTEXITCODE=0;try { & { "+source+" };if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}}catch{[Console]::Error.WriteLine($_.ToString());if($_.Exception -is [System.Management.Automation.CommandNotFoundException]){exit 127};exit 1}"
 # Guest-side receipts survive a VM reboot even if the host loses the reply.
 key=hashlib.sha256((tid+'|'+role+'|'+str(getattr(vm,'command_revision',0))+'|'+source).encode()).hexdigest()
 receipt=getattr(vm,'receipt_root',r'C:\MOA\receipts')+'\\'+key+'.json'
 replay='$true' if getattr(vm,'recovery_replay',False) else '$false'
 source64=base64.b64encode(source.encode('utf-8')).decode()
 script=receipt_script(workspace,receipt,source64,replay)
 pid=rpc(vm,'guest-exec',{'path':'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe','arg':['-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],'capture-output':True})['pid']
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  vm.guest_active(tid);result=rpc(vm,'guest-exec-status',{'pid':pid})
  if result.get('exited'):
   stdout,stderr=(base64.b64decode(result.get(key,''),validate=True).decode('utf-8',errors='replace') for key in ('out-data','err-data'));data=stdout+stderr
   return {'command':source,'exit_code':result.get('exitcode',1),'output':data[:output_limit],'stdout':stdout[:output_limit],'stderr':stderr[:output_limit],'output_truncated':len(data)>output_limit or result.get('out-truncated',False) or result.get('err-truncated',False),'runner':'qemu-windows-powershell','workspace':workspace,'guest_os':'windows'}
  time.sleep(.2)
 raise ValueError('Windows VM 명령 시간이 초과되었습니다. 실행 결과가 불명확하므로 자동 재실행하지 않습니다.')

def create_disk(root):
 import subprocess
 folder=root/'runtime/vm-images';folder.mkdir(parents=True,exist_ok=True)
 if folder.resolve()!=root.resolve()/'runtime/vm-images':raise ValueError('VM 디스크 경로를 확인하세요.')
 binary=root/'runtime/qemu/qemu-img.exe'
 if not binary.is_file():raise ValueError('프로젝트 QEMU 런타임(qemu-img.exe)을 먼저 설치하세요.')
 path=folder/('windows-'+uuid.uuid4().hex[:12]+'.qcow2')
 result=subprocess.run([str(binary),'create','-f','qcow2',str(path),'64G'],capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 if result.returncode or not path.is_file():raise ValueError('빈 Windows 디스크 생성을 확인하지 못했습니다.')
 return {'path':path.relative_to(root).as_posix(),'os_installed':False,'capacity_gib':64}

def export_workspace(vm,tid,role,workspace):
 source="""$root=(Get-Location).Path;$items=@(Get-ChildItem -LiteralPath $root -Recurse -Force -ErrorAction Stop);if($items|Where-Object {$_.Attributes -band [IO.FileAttributes]::ReparsePoint}){throw 'Links are not exportable'};$items=@($items|Where-Object {!$_.PSIsContainer});if($items.Count -gt 512 -or ($items|Measure-Object Length -Sum).Sum -gt 16000000){throw 'Export size exceeded'};$rows=@($items|ForEach-Object {@{path=$_.FullName.Substring($root.Length+1).Replace('\\','/');data=[Convert]::ToBase64String([IO.File]::ReadAllBytes($_.FullName))}});ConvertTo-Json -InputObject $rows -Compress -Depth 4"""
 result=command(vm,tid,role,source,output_limit=23000000)
 if result['exit_code'] or result['output_truncated']:raise ValueError('Windows VM 결과 파일 내보내기를 확인하지 못했습니다.')
 rows=json.loads(result.get('stdout',result['output']));buffer=io.BytesIO();total=0
 if not isinstance(rows,list) or len(rows)>512:raise ValueError('Windows VM 파일 목록 형식 오류')
 with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
  for row in rows:
   name=row['path'];parts=name.replace('\\','/').split('/')
   if not name or name.startswith('/') or ':' in name or '..' in parts:raise ValueError('Windows VM 결과 경로 오류')
   data=base64.b64decode(row['data'],validate=True);total+=len(data)
   if total>16000000:raise ValueError('Windows VM 결과 크기 초과')
   info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
 path=workspace.path('guest-workspace.tar.gz')
 if path.exists() and (path.is_symlink() or path.stat().st_nlink!=1):raise ValueError('VM 결과 경로 링크 오류')
 path.write_bytes(buffer.getvalue())
 return {'path':path.name,'bytes':path.stat().st_size,'runner':'qemu-windows-powershell'}
