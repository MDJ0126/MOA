param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
if ($Port -eq 8765) { New-Item -ItemType File -Path (Join-Path $PSScriptRoot 'data\supervisor.stop') -Force | Out-Null }
try { Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/control" -Method Post -ContentType 'application/json' -Headers @{'X-Local-Agent'='1'} -Body '{"action":"shutdown"}' }
catch { Write-Host '서버 응답이 없습니다. 실행 중인 감시 관리자에는 종료 요청을 남겼습니다.' }
Write-Host '새 작업을 중지하고 서버를 종료합니다. 진행 중 역할은 최대 250초까지 기다린 뒤 중단 상태로 보존됩니다.'
