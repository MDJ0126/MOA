param([string]$PythonExe)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class MoaTrayProbe {
 [StructLayout(LayoutKind.Sequential)] public struct ID {public uint cbSize; public IntPtr hWnd; public uint uID; public Guid guidItem;}
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int left,top,right,bottom;}
 [DllImport("shell32.dll")] public static extern int Shell_NotifyIconGetRect(ref ID id,out RECT rect);
 [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr hIcon);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern bool MoveFileEx(string oldName,string newName,int flags);
}
"@
$ProjectRoot=Split-Path $PSScriptRoot -Parent
$DataRoot=Join-Path $ProjectRoot 'data'
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
$hash=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant()))).Replace('-','').Substring(0,24)
$created=$false
$mutex=New-Object Threading.Mutex($true,('Local\MOA-Tray-'+$hash),[ref]$created)
if(-not $created){$mutex.Dispose();exit}
$script:closing=$false
$script:hadBackend=$false
$script:backend=$null
$script:lastError=''
$StatusPath=Join-Path $DataRoot 'tray.json'
$TrayStopPath=Join-Path $DataRoot ('tray-stop-'+$PID+'.request')
function Write-TrayStatus($Value) {
 $temporary=Join-Path $DataRoot ('.tray-'+[Guid]::NewGuid().ToString('N')+'.tmp')
 try{
  $json=$Value | ConvertTo-Json -Depth 4
  [IO.File]::WriteAllText($temporary,$json,(New-Object Text.UTF8Encoding($false)))
  for($attempt=0;$attempt -lt 5;$attempt++){
   if([MoaTrayProbe]::MoveFileEx($temporary,$StatusPath,9)){return}
   Start-Sleep -Milliseconds 25
  }
 }catch{}finally{if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary -ErrorAction SilentlyContinue}}
}
Add-Type -Path (Join-Path $PSScriptRoot 'TrayNotifications.cs')
$script:appID='MOA.LocalAgentTeam.'+$hash
$identityResult=[MoaTrayNotifications]::SetCurrentProcessExplicitAppUserModelID($script:appID)
if($identityResult -ne 0){throw ('모아 알림 앱 식별자 설정 실패: '+$identityResult)}
$identityKey=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Software\Classes\AppUserModelId\'+$script:appID)
try{
 $identityKey.SetValue('DisplayName','모아',[Microsoft.Win32.RegistryValueKind]::String)
 $identityIcon=Join-Path $ProjectRoot 'runtime/desktop/moa.ico'
 if(-not (Test-Path -LiteralPath $identityIcon)){$identityIcon=Join-Path $ProjectRoot 'docs/assets/moa-logo.png'}
 $identityKey.SetValue('IconUri',$identityIcon,[Microsoft.Win32.RegistryValueKind]::String)
}finally{$identityKey.Dispose()}
$script:icon=New-Object Windows.Forms.NotifyIcon
$bitmap=New-Object Drawing.Bitmap((Join-Path $ProjectRoot 'docs/assets/moa-logo.png'))
$iconHandle=$bitmap.GetHicon()
$managedIcon=[Drawing.Icon]::FromHandle($iconHandle).Clone()
[MoaTrayProbe]::DestroyIcon($iconHandle) | Out-Null
$notificationBitmap=New-Object Drawing.Bitmap($bitmap,[Windows.Forms.SystemInformation]::IconSize)
$notificationHandle=$notificationBitmap.GetHicon()
$notificationIcon=[Drawing.Icon]::FromHandle($notificationHandle).Clone()
[MoaTrayProbe]::DestroyIcon($notificationHandle) | Out-Null
$notificationBitmap.Dispose();$bitmap.Dispose()
$script:icon.Icon=$managedIcon
$script:icon.Text='모아 · 시작 중'
$menu=New-Object Windows.Forms.ContextMenuStrip
$open=$menu.Items.Add('모아 열기')
$script:statusItem=$menu.Items.Add('상태 확인 중');$script:statusItem.Enabled=$false
$script:pause=$menu.Items.Add('일시 중지')
$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator)) | Out-Null
$exitItem=$menu.Items.Add('모아 종료')
$script:icon.ContextMenuStrip=$menu
function Open-Moa {
 $shell=Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
 try {
  $launch='desktop-launch-'+[Guid]::NewGuid().ToString('N')
  Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-STA','-WindowStyle','Hidden','-File',('"'+(Join-Path $PSScriptRoot 'desktop.ps1')+'"')) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $DataRoot ($launch+'.stdout.log')) -RedirectStandardError (Join-Path $DataRoot ($launch+'.stderr.log')) | Out-Null
 }catch{$script:lastError=$_.Exception.Message}
}
$script:nextNotificationPoll=0
$script:lastNotification=$null
function Receive-CompletionNotifications($State) {
 if($script:closing -or $State.desktop_notifications.enabled -eq $false){return}
 $now=[DateTimeOffset]::Now.ToUnixTimeSeconds()
 if($now -lt $script:nextNotificationPoll){return}
 $script:nextNotificationPoll=$now+6
 $headers=@{'X-Local-Agent'='1'}
 $batch=Invoke-RestMethod 'http://127.0.0.1:8765/api/notifications' -Method Post -Headers $headers -ContentType 'application/json' -Body '{"action":"claim"}' -TimeoutSec 3
 $items=@($batch.items | Where-Object {$null -ne $_})
 if(-not $items.Count){return}
 $title=if($items.Count -eq 1 -and $items[0].kind -eq 'test'){'모아 · 테스트 알림'}elseif($items.Count -eq 1){'모아 · 작업 완료'}else{'모아 · 작업 '+$items.Count+'건 완료'}
 $body=(@($items | Select-Object -First 3 | ForEach-Object {$_.title}) -join "`n")
 if($items.Count -gt 3){$body+="`n외 "+($items.Count-3)+'건'}
 if($body.Length -gt 240){$body=$body.Substring(0,237)+'…'}
 $success=$false
 try {
  $script:lastNotification=@{state='requested';kind=$items[0].kind;count=$items.Count;requested=$now;shown=$null}
  [MoaTrayNotifications]::Show($script:icon,$title,$body,$notificationIcon.Handle)
  $script:lastNotification.icon='moa';$script:lastNotification.app_id=$script:appID
  $success=$true
 }finally{
  $ack=@{action='ack';token=$batch.token;success=$success} | ConvertTo-Json -Compress
  Invoke-RestMethod 'http://127.0.0.1:8765/api/notifications' -Method Post -Headers $headers -ContentType 'application/json' -Body $ack -TimeoutSec 3 | Out-Null
 }
}
$script:icon.Add_BalloonTipShown({if($script:lastNotification){$script:lastNotification.state='shown';$script:lastNotification.shown=[DateTimeOffset]::Now.ToUnixTimeSeconds()}})
$script:icon.Add_BalloonTipClicked({Open-Moa})
function Get-Backend {
 $registration=Join-Path $ProjectRoot 'runtime/app-8765.json'
 if(-not (Test-Path -LiteralPath $registration)){return $null}
 $owned=Get-Content -LiteralPath $registration -Raw | ConvertFrom-Json
 if($owned.root -ne $ProjectRoot -or $owned.port -ne 8765){throw '현재 프로젝트의 실행 기록과 일치하지 않습니다.'}
 Get-Process -Id $owned.pid -ErrorAction Stop | Out-Null
 Invoke-RestMethod 'http://127.0.0.1:8765/api/state' -TimeoutSec 2
}
$open.Add_Click({Open-Moa})
$script:icon.Add_DoubleClick({Open-Moa})
$script:pause.Add_Click({
 try{
  $state=Get-Backend
  if(-not $state){return}
  $action=if($state.paused){'resume'}else{'pause'}
  Invoke-RestMethod 'http://127.0.0.1:8765/api/control' -Method Post -Headers @{'X-Local-Agent'='1'} -ContentType 'application/json' -Body ('{"action":"'+$action+'"}') -TimeoutSec 3 | Out-Null
 }catch{$script:lastError=$_.Exception.Message}
})
$exitItem.Add_Click({
 Set-Content (Join-Path $DataRoot 'desktop-close.request') close
 $script:closing=$true;$script:icon.Text='모아 · 종료 중';$script:statusItem.Text='업무 정리 후 종료합니다';$script:pause.Enabled=$false
 $null=Start-Process -FilePath $PythonExe -ArgumentList @(('"'+(Join-Path $ProjectRoot 'src/stop_app.py')+'"'),'--port','8765') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
})
$timer=New-Object Windows.Forms.Timer;$timer.Interval=2000
$timer.Add_Tick({
 if(Test-Path -LiteralPath $TrayStopPath){Remove-Item -LiteralPath $TrayStopPath;[Windows.Forms.Application]::ExitThread();return}
 try{
  $state=Get-Backend
  if($state){
   $script:hadBackend=$true
   Receive-CompletionNotifications $state
   $count=@($state.tasks | Where-Object {$_.status -in @('queued','running')}).Count
   $label=if($script:closing){'종료 중'}elseif($state.paused){'일시 중지'}elseif($count){'업무 진행 중 · '+$count+'건'}else{'업무 대기 중'}
   $script:statusItem.Text=$label;$script:pause.Text=if($state.paused){'계속 실행'}else{'일시 중지'};$script:icon.Text='모아 · '+$label
  }elseif($script:hadBackend -and $script:closing){[Windows.Forms.Application]::ExitThread()}
  elseif($script:backend -and $script:backend.HasExited){$script:lastError='백그라운드 실행이 종료됐습니다. data 로그를 확인해 주세요.'}
 }catch{if($script:closing -and $script:hadBackend){[Windows.Forms.Application]::ExitThread()}else{$script:lastError=$_.Exception.Message}}
 $rect=$null
 try{
  $flags=[Reflection.BindingFlags]'Instance,NonPublic'
  $idField=$script:icon.GetType().GetField('_id',$flags);if(-not $idField){$idField=$script:icon.GetType().GetField('id',$flags)}
  $windowField=$script:icon.GetType().GetField('_window',$flags);if(-not $windowField){$windowField=$script:icon.GetType().GetField('window',$flags)}
  $identifier=New-Object MoaTrayProbe+ID;$identifier.cbSize=[Runtime.InteropServices.Marshal]::SizeOf($identifier);$identifier.uID=$idField.GetValue($script:icon);$identifier.hWnd=$windowField.GetValue($script:icon).Handle
  $bounds=New-Object MoaTrayProbe+RECT
  if([MoaTrayProbe]::Shell_NotifyIconGetRect([ref]$identifier,[ref]$bounds) -eq 0){$rect=@{left=$bounds.left;top=$bounds.top;right=$bounds.right;bottom=$bounds.bottom}}
 }catch{}
 Write-TrayStatus @{pid=$PID;visible=$script:icon.Visible;icon_rect=$rect;status=$script:statusItem.Text;error=$script:lastError;last_notification=$script:lastNotification;updated=[DateTimeOffset]::Now.ToUnixTimeSeconds()}
})
try{
 try{$existing=Get-Backend}catch{$existing=$null}
 if(-not $existing){$script:backend=Start-Process -FilePath $PythonExe -ArgumentList ('"'+(Join-Path $ProjectRoot 'src/supervisor.py')+'"') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $DataRoot 'tray-backend.stdout.log') -RedirectStandardError (Join-Path $DataRoot 'tray-backend.stderr.log') -PassThru}
 else{$script:hadBackend=$true}
 $script:icon.Visible=$true;$timer.Start()
 [Windows.Forms.Application]::Run()
}finally{
 $timer.Stop();$timer.Dispose();$script:icon.Visible=$false;$script:icon.Dispose();$menu.Dispose();$notificationIcon.Dispose();$managedIcon.Dispose();$mutex.ReleaseMutex();$mutex.Dispose()
 Write-TrayStatus @{pid=$PID;visible=$false;status='종료됨';updated=[DateTimeOffset]::Now.ToUnixTimeSeconds()}
}
