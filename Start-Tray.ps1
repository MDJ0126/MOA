$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'scripts/PythonRuntime.ps1')
$TrayPython=Resolve-MoaPython
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'data') -Force | Out-Null
$TrayShell=(Get-Process -Id $PID).Path
$TrayScript=Join-Path $PSScriptRoot 'src/tray.ps1'
$arguments=@('-NoProfile','-STA','-WindowStyle','Hidden','-File',('"'+$TrayScript+'"'),'-PythonExe',('"'+$TrayPython+'"'))
$TrayProcess=Start-Process -FilePath $TrayShell -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data/tray.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data/tray.stderr.log') -PassThru
Write-Host '모아를 시스템 트레이에서 실행합니다. 아이콘을 더블클릭하면 관리 창이 열립니다.'
Write-Host ('트레이 실행 PID: '+$TrayProcess.Id)
