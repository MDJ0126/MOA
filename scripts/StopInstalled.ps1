param()
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$data=Join-Path $root 'data'
if(Test-Path -LiteralPath $data){
 Set-Content -LiteralPath (Join-Path $data 'desktop-close.request') -Value close
 $tray=Join-Path $data 'tray.json'
 if(Test-Path -LiteralPath $tray){try{$owner=Get-Content -LiteralPath $tray -Raw | ConvertFrom-Json;if($owner.pid -gt 0){Set-Content -LiteralPath (Join-Path $data ('tray-stop-'+$owner.pid+'.request')) -Value stop}}catch{}}
}
$registration=Join-Path $root 'runtime/app-8765.json'
if(Test-Path -LiteralPath $registration){
 $owner=Get-Content -LiteralPath $registration -Raw | ConvertFrom-Json
 if([IO.Path]::GetFullPath($owner.root) -eq [IO.Path]::GetFullPath($root)){
  $python=Join-Path $root 'runtime/python/python.exe'
  if(Test-Path -LiteralPath $python){& $python (Join-Path $root 'src/stop_app.py') --port 8765 | Out-Null}
 }
}
$deadline=(Get-Date).AddSeconds(20)
do {
 $windows=@(Get-Process -Name MOA -ErrorAction SilentlyContinue | Where-Object {$_.Path -like ($root+'\*')})
 if(-not $windows.Count){exit 0}
 Start-Sleep -Milliseconds 300
}while((Get-Date) -lt $deadline)
exit 1
