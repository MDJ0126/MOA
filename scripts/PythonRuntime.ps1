function Resolve-MoaPython {
    param([string]$PythonPath = $env:MOA_PYTHON)
    $ExplicitPython = -not [string]::IsNullOrWhiteSpace($PythonPath)
    $Candidates = @()
    if ($ExplicitPython) { $Candidates += $PythonPath }
    else {
        $Bundled = Join-Path (Split-Path $PSScriptRoot -Parent) 'runtime/python/python.exe'
        if(Test-Path -LiteralPath $Bundled){$Candidates += $Bundled}
        $Command = Get-Command python -ErrorAction SilentlyContinue
        if ($Command) { $Candidates += $Command.Source }
        $Candidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
    }
    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { continue }
        if ($Candidate -like '*\Microsoft\WindowsApps\python.exe') { continue }
        try {
            $Probe = & $Candidate -I -c 'import sys,json,sqlite3,ssl; print(json.dumps(dict(version=list(sys.version_info[:3]),executable=sys.executable))); sys.exit(0 if sys.version_info >= (3,11) else 3)' 2>&1
            if ($LASTEXITCODE -eq 0) {
                $Info = ($Probe -join "`n") | ConvertFrom-Json -ErrorAction Stop
                if ($Info.executable) { return [string]$Info.executable }
            }
        } catch { }
    }
    if ($ExplicitPython) { throw 'MOA_PYTHON 경로에서 Python 3.11 이상과 sqlite3/ssl을 확인하지 못했습니다. 실행 파일 경로를 확인하세요.' }
    throw 'Python 3.11 이상을 설치하고 PATH에 등록하거나 MOA_PYTHON에 python.exe 전체 경로를 지정하세요.'
}
