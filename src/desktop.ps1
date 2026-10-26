param([switch]$SmokeTest)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
. (Join-Path $root 'scripts/PrepareDesktop.ps1') -ProjectRoot $root
$exe=& (Join-Path $root 'scripts/BuildDesktop.ps1') -ProjectRoot $root
$url='https://app.slack.com/'
try {$state=Invoke-RestMethod 'http://127.0.0.1:8765/api/state' -TimeoutSec 3;$workspace=$state.slack_auto.accounts | Where-Object {$_.team_id -match '^T[A-Z0-9]+$'} | Select-Object -First 1;if($workspace){$url='https://app.slack.com/client/'+$workspace.team_id}}catch{}
$process=Start-Process -FilePath $exe -ArgumentList @(('"'+$root+'"'),('"'+$url+'"'),$SmokeTest.ToString()) -WindowStyle Normal -PassThru
$process.WaitForExit()
if($process.ExitCode -ne 0){throw ('MOA 창 실행 실패: '+$process.ExitCode)}
