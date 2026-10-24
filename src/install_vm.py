"""Project-local QEMU and a lightweight demo desktop; no drivers or registry edits."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
CACHE=ROOT/'runtime'/'vm-downloads'
def require_stopped_vm():
    from vm_runtime import pid_alive
    session=ROOT/'data/vm/session.json'
    try:value=json.loads(session.read_text(encoding='utf-8'))
    except (OSError,ValueError):return
    if pid_alive(value.get('pid')):raise ValueError('공유 VM 전원을 끈 뒤 설치 또는 이미지 재구성을 실행하세요.')
def fetch(url,path):
    if not path.exists():
        temp=path.with_suffix(path.suffix+'.part')
        try:
            with urllib.request.urlopen(url,timeout=60) as response,temp.open('wb') as output:
                while block:=response.read(1024*1024):output.write(block)
        except urllib.error.URLError:
            # Use Windows' trusted certificate store via PowerShell; never disable TLS checks.
            subprocess.run([shutil.which('pwsh') or 'powershell.exe','-NoProfile','-File',str(ROOT/'scripts/Download-VM.ps1'),'-Uri',url,'-OutFile',str(temp)],check=True,stdout=subprocess.DEVNULL)
        temp.replace(path)
    return path
def main():
    require_stopped_vm()
    CACHE.mkdir(parents=True,exist_ok=True)
    api='https://api.github.com/repos/ip7z/7zip/releases/tags/26.05'
    with urllib.request.urlopen(api,timeout=30) as response:assets=json.load(response)['assets']
    sources=[('qemu-w64-setup-20260811.exe','https://qemu.weilnetz.de/w64/qemu-w64-setup-20260811.exe'),
             ('TinyCore-17.1.iso','https://distro.ibiblio.org/tinycorelinux/17.x/x86/release/TinyCore-17.1.iso')]
    sources += [(name,next(a['browser_download_url'] for a in assets if a['name']==name)) for name in ('7zr.exe','7z2605-x64.exe')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda pair:fetch(pair[1],CACHE/pair[0]),sources))
    expected='5bcf9eed634e8575a37b74f445af41a2fe4106da512d0c30c368301d4c105037fdfab40a5287367a28a957624cddebbc8c07e16c88ab6634f554cdf3d16bf543'
    if hashlib.sha512((CACHE/sources[0][0]).read_bytes()).hexdigest()!=expected:raise ValueError('QEMU SHA-512 mismatch')
    for name in ('7zr.exe','7z2605-x64.exe'):
        digest=next(a.get('digest') for a in assets if a['name']==name)
        if not digest or not digest.startswith('sha256:'):raise RuntimeError('Release asset has no SHA-256 digest')
        if hashlib.sha256((CACHE/name).read_bytes()).hexdigest()!=digest.split(':')[1]:raise ValueError(name+' hash mismatch')
    md5=fetch(sources[1][1]+'.md5.txt',CACHE/'guest.md5').read_text().split()[0]
    if hashlib.md5((CACHE/'TinyCore-17.1.iso').read_bytes()).hexdigest()!=md5:raise ValueError('Guest ISO checksum mismatch')
    seven=CACHE/'7zip';seven.mkdir(exist_ok=True)
    subprocess.run([str(CACHE/'7zr.exe'),'x','-y',str(CACHE/'7z2605-x64.exe'),'-o'+str(seven)],check=True,stdout=subprocess.DEVNULL)
    qemu=ROOT/'runtime'/'qemu';qemu.mkdir(exist_ok=True)
    subprocess.run([str(seven/'7z.exe'),'x','-y',str(CACHE/sources[0][0]),'-o'+str(qemu)],check=True,stdout=subprocess.DEVNULL)
    executable=qemu/'qemu-system-x86_64.exe'
    if not executable.exists():raise RuntimeError('QEMU executable missing after extraction')
    version=subprocess.check_output([str(executable),'--version'],text=True).splitlines()[0]
    manifest={'version':version,'sources':[{'name':n,'url':u,'sha256':hashlib.sha256((CACHE/n).read_bytes()).hexdigest()} for n,u in sources],
              'qemu_verified_sha512':expected,'guest_verified_md5':md5,'guest_note':'Live demo desktop; not a Windows or engine environment'}
    (qemu/'installed.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(version,flush=True)
    print('PASS: QEMU SHA-512, extraction tool release SHA-256, guest published MD5; project-local extraction complete',flush=True)
if __name__=='__main__':main()
