param([string]$ProjectRoot=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
$version='1.0.4314.35'
$base=Join-Path $ProjectRoot 'runtime/webview2'
$package=Join-Path $base $version
$core=Join-Path $package 'lib/net462/Microsoft.Web.WebView2.Core.dll'
if(Test-Path -LiteralPath $core){return}
New-Item -ItemType Directory -Path $base -Force | Out-Null
$archive=Join-Path $base ('webview2-'+[Guid]::NewGuid().ToString('N')+'.zip')
try {
 [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
 Invoke-WebRequest ('https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/'+$version+'/microsoft.web.webview2.'+$version+'.nupkg') -OutFile $archive -UseBasicParsing
 Expand-Archive -LiteralPath $archive -DestinationPath $package -Force
 if(-not (Test-Path -LiteralPath $core)){throw 'WebView2 패키지를 확인하지 못했습니다.'}
} finally {if(Test-Path -LiteralPath $archive){Remove-Item -LiteralPath $archive}}
