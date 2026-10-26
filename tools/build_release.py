"""Build a clean, self-contained Windows application payload and NSIS installer."""
import hashlib,json,os,re,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION='0.1.0'
STAGE=ROOT/'runtime/release-staging'/('MOA-'+VERSION)
DIST=ROOT/'dist'

def copy(source,target):
 target=STAGE/target;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/source,target)

def tree(source,target):
 for p in (ROOT/source).rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.log'):
   if p.is_symlink():raise ValueError('Linked release input: '+str(p))
   copy(p.relative_to(ROOT),Path(target)/p.relative_to(ROOT/source))

def build():
 if STAGE.exists():
  if not STAGE.resolve().is_relative_to((ROOT/'runtime/release-staging').resolve()):raise ValueError('Unsafe stage')
  shutil.rmtree(STAGE)
 STAGE.mkdir(parents=True);DIST.mkdir(exist_ok=True)
 tree('src','src');tree('scripts','scripts');tree('docs','docs')
 for doc in (STAGE/'docs').rglob('*'):
  if doc.suffix in ('.html','.md','.json'):
   text=doc.read_text(encoding='utf-8-sig');text=re.sub(r'\b([TCUD])0[A-Z0-9]{8,}\b',lambda m:m[1]+'0123456789',text);text=re.sub(r'xox([bap])-[A-Za-z0-9-]+',lambda m:'xox'+m[1]+'-YOUR-TOKEN',text);doc.write_text(text,encoding='utf-8')
 for name in ['Start.ps1','Start-Tray.ps1','Install.ps1','requirements.txt','README.ko.md','README.md']:
  copy(name,name)
 v=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
 allowed=('model','model_description','model_url','default_mode','model_file','runtime_executable','threads','context_size','max_revisions','cache_ram_mib','sleep_idle_seconds','prestart_vm','compact_guest_prompts')
 config={k:v[k] for k in allowed if k in v};config.update(engine={},slack={})
 (STAGE/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
 python=STAGE/'runtime/python';python.mkdir(parents=True)
 with zipfile.ZipFile(ROOT/'runtime/build-tools/python-3.13.9-embed-amd64.zip') as z:z.extractall(python)
 (python/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../../src\n../pythonlibs\nimport site\n',encoding='ascii')
 site=python/'Lib/site-packages';site.mkdir(parents=True)
 wheels=[]
 for wheel in sorted((ROOT/'runtime/build-tools/wheels').glob('*.whl')):
  with zipfile.ZipFile(wheel) as z:z.extractall(site)
  wheels.append({'name':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()})
 copy('runtime/llama-b11669.zip','runtime/llama-b11669.zip')
 version='1.0.4314.35';base=Path('runtime/webview2')/version
 for relative in ['lib/net462/Microsoft.Web.WebView2.Core.dll','lib/net462/Microsoft.Web.WebView2.WinForms.dll','runtimes/win-x64/native/WebView2Loader.dll']:
  copy(base/relative,base/relative);copy(base/relative,Path('runtime/desktop')/Path(relative).name)
 for p in (ROOT/base).rglob('*'):
  if p.is_file() and p.name.lower() in ('license.txt','license.md','notice.txt'):copy(p.relative_to(ROOT),Path('licenses/webview2')/p.name)
 copy('runtime/desktop/moa.ico','runtime/desktop/moa.ico')
 compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
 refs=['/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll']
 out=STAGE/'runtime/desktop'
 subprocess.run([str(compiler),'/nologo','/codepage:65001','/target:winexe','/platform:x64','/out:'+str(out/'MOA.exe'),'/win32icon:'+str(out/'moa.ico'),*refs,'/reference:'+str(out/'Microsoft.Web.WebView2.Core.dll'),'/reference:'+str(out/'Microsoft.Web.WebView2.WinForms.dll'),str(STAGE/'src/DesktopWindow.cs')],check=True)
 signature=hashlib.sha256((STAGE/'src/DesktopWindow.cs').read_bytes()).hexdigest().upper()+hashlib.sha256((STAGE/'docs/assets/moa-logo.png').read_bytes()).hexdigest().upper()+'-native-v1'
 (out/'build.signature').write_text(signature,encoding='utf-8')
 subprocess.run([str(compiler),'/nologo','/codepage:65001','/target:winexe','/platform:x64','/out:'+str(STAGE/'MOA.exe'),'/win32icon:'+str(out/'moa.ico'),*refs,str(STAGE/'src/DistributionLauncher.cs')],check=True)
 for path in [STAGE/'MOA.exe',out/'MOA.exe']:
  (path.with_suffix('.exe.config')).write_text('<?xml version="1.0"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.6.2"/></startup></configuration>',encoding='utf-8')
 (STAGE/'배포 안내.txt').write_text('MOA 0.1.0 · Windows x64\n\nMOA.exe를 실행하세요. Python과 필수 라이브러리가 포함되어 있습니다.\n첫 실행에서 무료 AI 모델(약 4.7GB)과 선택한 VM 환경을 내려받습니다.\n인터넷 연결과 모델·VM을 포함해 약 10GB 이상의 여유 공간을 권장합니다.\nSlack·Git 인증은 각 사용자의 계정으로 연결하세요.\n삭제 시 사용자 데이터와 모델은 보존합니다.\n이 설치 파일은 코드서명 인증서로 서명되어 있지 않습니다.\n',encoding='utf-8-sig')
 (STAGE/'THIRD-PARTY-NOTICES.txt').write_text('MOA bundled components\n\nPython 3.13.9: Python Software Foundation License (runtime/python/LICENSE.txt)\nhttps://www.python.org/\n\nPillow 12.0.0, slack-sdk 3.45.0, pywinpty 3.1.0, pycdlib 1.14.0:\nLicense texts are included in runtime/python/Lib/site-packages/*.dist-info/\n\nMicrosoft WebView2 SDK 1.0.4314.35: Microsoft redistribution license\nhttps://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4314.35\n\nllama.cpp b11669: MIT, upstream license in licenses/\nhttps://github.com/ggml-org/llama.cpp/tree/b11669\nLLVM OpenMP license is inside runtime/llama-b11669.zip\n\nAI model and VM components are downloaded separately from their official sources; their licenses and source URLs remain in the installation manifests.\nNSIS installer framework: https://nsis.sourceforge.io/Docs/AppendixI.html\n',encoding='utf-8')
 if (ROOT/'packaging/licenses').exists():tree('packaging/licenses','licenses')
 for script in STAGE.rglob('*.ps1'):script.write_text(script.read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
 files=[]
 for p in sorted(STAGE.rglob('*')):
  if p.is_file():files.append({'path':p.relative_to(STAGE).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest()})
 manifest={'version':VERSION,'python':'3.13.9','wheels':wheels,'files':files}
 (STAGE/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 (DIST/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 # Uninstall only files shipped by this release, preserving runtime data, model and browser profiles.
 uninstall=[]
 for f in files+[{'path':'release-manifest.json'}]:uninstall.append('Delete "$INSTDIR\\'+f['path'].replace('/','\\')+'"')
 dirs=sorted({str(Path(f['path']).parent).replace('/','\\') for f in files if str(Path(f['path']).parent)!='.'},key=lambda p:p.count('\\'),reverse=True)
 uninstall += ['RMDir "$INSTDIR\\'+d+'"' for d in dirs]
 (ROOT/'runtime/release-staging/uninstall-files.nsh').write_text('\n'.join(uninstall),encoding='utf-8-sig')
 print(json.dumps({'stage':str(STAGE),'files':len(files),'bytes':sum(f['bytes'] for f in files)},ensure_ascii=False))
if __name__=='__main__':build()
