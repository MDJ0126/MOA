"""First-run preparation for the redistributable Windows package."""
import argparse,json,os,subprocess,sys,urllib.request,winreg
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def webview_installed():
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for flags in (0,winreg.KEY_WOW64_32KEY,winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\EdgeUpdate\Clients',0,winreg.KEY_READ|flags) as parent:
                    for i in range(winreg.QueryInfoKey(parent)[0]):
                        with winreg.OpenKey(parent,winreg.EnumKey(parent,i)) as item:
                            try:
                                if 'webview2' in winreg.QueryValueEx(item,'name')[0].lower() and winreg.QueryValueEx(item,'pv')[0]!='0.0.0.0':return True
                            except OSError:pass
            except OSError:pass
    return False

def ensure_webview():
    if webview_installed():print('WebView2 준비 완료',flush=True);return
    target=ROOT/'runtime/MicrosoftEdgeWebview2Setup.exe'
    print('Microsoft 공식 WebView2 런타임을 준비합니다.',flush=True)
    with urllib.request.urlopen('https://go.microsoft.com/fwlink/p/?LinkId=2124703',timeout=60) as source,target.open('wb') as dest:
        while chunk:=source.read(1024*1024):dest.write(chunk)
    script="$s=Get-AuthenticodeSignature -LiteralPath '"+str(target).replace("'","''")+"';if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){exit 2}"
    shell=Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.run([str(shell),'-NoProfile','-Command',script],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.run([str(target),'/silent','/install'],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if not webview_installed():raise ValueError('WebView2 설치 후 준비 상태를 확인하지 못했습니다.')

def ensure_visual_cpp():
    system=Path(os.environ['WINDIR'])/'System32'
    if all((system/name).is_file() for name in ('msvcp140.dll','vcruntime140.dll')):return
    print('Microsoft Visual C++ 런타임을 준비합니다. Windows 권한 확인이 표시될 수 있습니다.',flush=True)
    target=ROOT/'runtime/vc_redist.x64.exe'
    with urllib.request.urlopen('https://aka.ms/vs/17/release/vc_redist.x64.exe',timeout=60) as source,target.open('wb') as dest:
        while chunk:=source.read(1024*1024):dest.write(chunk)
    escaped=str(target).replace("'","''")
    script="$p='"+escaped+"';$s=Get-AuthenticodeSignature -LiteralPath $p;if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){exit 2};$r=Start-Process -FilePath $p -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait -PassThru;if($r.ExitCode -notin 0,3010){exit $r.ExitCode}"
    shell=system/'WindowsPowerShell/v1.0/powershell.exe'
    subprocess.run([str(shell),'-NoProfile','-Command',script],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if not (system/'msvcp140.dll').is_file():raise ValueError('Windows C++ 런타임 설치를 확인하지 못했습니다.')

def verify():
    import sqlite3,ssl,slack_sdk,winpty,pycdlib
    from PIL import Image
    result={'python':sys.version.split()[0],'python_path':sys.executable,'sqlite':sqlite3.sqlite_version,'ssl':bool(ssl.OPENSSL_VERSION),'webview':webview_installed(),'root':str(ROOT)}
    print(json.dumps(result,ensure_ascii=False),flush=True);return result

def prepare(with_vm=False):
    ensure_visual_cpp();ensure_webview();verify()
    import install_model
    install_model.main()
    if with_vm:
        print('무료 VM 작업 환경을 준비합니다.',flush=True)
        import install_vm,build_vm_desktop
        install_vm.main();build_vm_desktop.main()
        from app import Store
        from setup_config import write_setting
        from vm_runtime import DEFAULT
        write_setting(Store(ROOT/'data'),'vm',{**DEFAULT,'enabled':True})
    print('MOA 준비 완료',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');parser.add_argument('--with-vm',action='store_true');args=parser.parse_args()
    try:verify() if args.verify else prepare(args.with_vm)
    except Exception as error:print('준비 실패: '+str(error),flush=True);raise SystemExit(1)
