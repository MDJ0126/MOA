param([string]$Model = $env:AGENT_MODEL, [int]$Port = 8765, [switch]$Direct)
$ErrorActionPreference = 'Stop'
$BundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $BundledPython) { $PythonExe = $BundledPython }
else { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
if (-not $Direct -and [string]::IsNullOrWhiteSpace($Model) -and $Port -eq 8765) {
    & $PythonExe (Join-Path $PSScriptRoot 'supervisor.py')
    exit $LASTEXITCODE
}
$LaunchArgs = @((Join-Path $PSScriptRoot 'app.py'), '--port', "$Port")
if (-not [string]::IsNullOrWhiteSpace($Model)) { $LaunchArgs += @('--model', $Model) }
& $PythonExe @LaunchArgs
