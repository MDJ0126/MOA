param([string]$Model = $env:AGENT_MODEL, [int]$Port = 8765, [switch]$Direct,[switch]$Demo,[switch]$Console)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts/PythonRuntime.ps1')
$PythonExe = Resolve-MoaPython
if ($Demo -and -not $PSBoundParameters.ContainsKey('Port')) { $Port = 8767 }
if (-not $Demo -and -not $Direct -and [string]::IsNullOrWhiteSpace($Model) -and $Port -eq 8765) {
    if (-not $Console) { & (Join-Path $PSScriptRoot 'Start-Tray.ps1'); exit $LASTEXITCODE }
    & $PythonExe (Join-Path $PSScriptRoot 'src/supervisor.py')
    exit $LASTEXITCODE
}
$LaunchArgs = @((Join-Path $PSScriptRoot 'src/app.py'), '--port', "$Port")
if ($Demo) { $LaunchArgs += '--demo'; Write-Host "데모 전용: http://127.0.0.1:$Port · data-demo에 저장 · 실제 AI/VM/Slack 미연결" }
elseif (-not [string]::IsNullOrWhiteSpace($Model)) { $LaunchArgs += @('--model', $Model) }
& $PythonExe @LaunchArgs
exit $LASTEXITCODE
