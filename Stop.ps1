param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts/PythonRuntime.ps1')
$PythonExe = Resolve-MoaPython
& $PythonExe (Join-Path $PSScriptRoot 'src/stop_app.py') --port "$Port"
exit $LASTEXITCODE
