param([switch]$DownloadModel,[switch]$WithVM)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'PythonRuntime.ps1')
$AgentPython = Resolve-MoaPython
& $AgentPython -m pip install --upgrade --target (Join-Path $PSScriptRoot 'runtime\pythonlibs') -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Slack 연결 도구 설치 실패' }
if ($DownloadModel) {
    & $AgentPython (Join-Path $PSScriptRoot 'install_model.py')
    if ($LASTEXITCODE -ne 0) { throw '모델 설치 실패' }
}
if ($WithVM) {
    & $AgentPython -m pip install --upgrade --target (Join-Path $PSScriptRoot 'runtime\pythonlibs') pycdlib==1.14.0
    if ($LASTEXITCODE -ne 0) { throw 'VM ISO 구성 도구 설치 실패' }
    & $AgentPython (Join-Path $PSScriptRoot 'install_vm.py')
    if ($LASTEXITCODE -ne 0) { throw 'VM 런타임 설치 실패' }
    & $AgentPython (Join-Path $PSScriptRoot 'build_vm_desktop.py')
    if ($LASTEXITCODE -ne 0) { throw 'VM 데스크톱 이미지 구성 실패' }
}
Write-Host '준비되었습니다. Start.ps1을 실행한 뒤 http://127.0.0.1:8765 에서 설정하세요.'
