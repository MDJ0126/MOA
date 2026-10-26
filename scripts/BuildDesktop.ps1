param([string]$ProjectRoot=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
function Get-MoaFileHash([string]$Path) {
 $stream=[IO.File]::OpenRead($Path);$sha=[Security.Cryptography.SHA256]::Create()
 try{return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}finally{$stream.Dispose();$sha.Dispose()}
}
$package=Join-Path $ProjectRoot 'runtime/webview2/1.0.4314.35'
$out=Join-Path $ProjectRoot 'runtime/desktop'
$exe=Join-Path $out 'MOA.exe'
New-Item -ItemType Directory -Path $out -Force | Out-Null
$source=Join-Path $ProjectRoot 'src/DesktopWindow.cs'
$logo=Join-Path $ProjectRoot 'docs/assets/moa-logo.png'
$signature=(Get-MoaFileHash $source)+(Get-MoaFileHash $logo)+'-native-v1'
$stamp=Join-Path $out 'build.signature'
if((Test-Path -LiteralPath $exe) -and (Test-Path -LiteralPath $stamp) -and (Get-Content -LiteralPath $stamp -Raw).Trim() -eq $signature){return $exe}
$running=@(Get-Process -Name MOA -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $exe})
if($running.Count){
 Set-Content -LiteralPath (Join-Path $ProjectRoot 'data/desktop-close.request') -Value close
 $running | Wait-Process -Timeout 15 -ErrorAction Stop
}
Add-Type -AssemblyName System.Drawing
$original=[Drawing.Image]::FromFile($logo)
$images=@();$sizes=@(16,24,32,48,64,128,256)
try{foreach($size in $sizes){
 $bitmap=New-Object Drawing.Bitmap($size,$size)
 $g=[Drawing.Graphics]::FromImage($bitmap);$g.InterpolationMode=[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
 $g.DrawImage($original,0,0,$size,$size);$g.Dispose()
 $stream=New-Object IO.MemoryStream;$bitmap.Save($stream,[Drawing.Imaging.ImageFormat]::Png);$bitmap.Dispose();$images+=,@($stream.ToArray());$stream.Dispose()
}}finally{$original.Dispose()}
$icon=Join-Path $out 'moa.ico';$stream=[IO.File]::Create($icon);$writer=New-Object IO.BinaryWriter($stream)
try{
 $writer.Write([UInt16]0);$writer.Write([UInt16]1);$writer.Write([UInt16]$sizes.Count);$offset=6+16*$sizes.Count
 for($i=0;$i -lt $sizes.Count;$i++){$dimension=if($sizes[$i] -eq 256){0}else{$sizes[$i]};$writer.Write([byte]$dimension);$writer.Write([byte]$dimension);$writer.Write([byte]0);$writer.Write([byte]0);$writer.Write([UInt16]1);$writer.Write([UInt16]32);$writer.Write([UInt32]$images[$i].Count);$writer.Write([UInt32]$offset);$offset+=$images[$i].Count}
 foreach($bytes in $images){$writer.Write([byte[]]$bytes)}
}finally{$writer.Dispose()}
$core=Join-Path $package 'lib/net462/Microsoft.Web.WebView2.Core.dll';$forms=Join-Path $package 'lib/net462/Microsoft.Web.WebView2.WinForms.dll'
foreach($dependency in @($core,$forms,(Join-Path $package 'runtimes/win-x64/native/WebView2Loader.dll'))){
 $destination=Join-Path $out (Split-Path $dependency -Leaf)
 if(-not (Test-Path -LiteralPath $destination) -or (Get-MoaFileHash $dependency) -ne (Get-MoaFileHash $destination)){Copy-Item -LiteralPath $dependency -Destination $destination -Force}
}
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$temp=Join-Path $out ('MOA-build-'+[Guid]::NewGuid().ToString('N')+'.exe')
& $compiler /nologo /target:winexe /platform:x64 ('/out:'+$temp) ('/win32icon:'+$icon) /reference:System.Windows.Forms.dll /reference:System.Drawing.dll ('/reference:'+$core) ('/reference:'+$forms) $source
if($LASTEXITCODE -ne 0){throw 'MOA 전용 실행 파일 빌드 실패'}
Move-Item -LiteralPath $temp -Destination $exe -Force
Set-Content -LiteralPath $stamp -Value $signature
return $exe
