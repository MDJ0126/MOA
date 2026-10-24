"""Install pinned official artifacts locally; verify publisher SHA-256 before use."""
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
import urllib.request
import urllib.error
import zipfile
from supervisor import acquire

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = [
    ('runtime/llama-b11669.zip', 'https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-win-cpu-x64.zip', '37b3efcf962847ca869745fd4ccf7f5e7e8dacfeff9fa3523b4ca8cdc66d632b'),
    ('models/qwen2.5-coder-7b-instruct-q4_k_m.gguf', 'https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf', '509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c'),
]

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def regular(path):
    if path.is_symlink() or getattr(path,'is_junction',lambda:False)() or (path.exists() and (not path.is_file() or path.stat().st_nlink!=1)):
        raise ValueError('설치 파일의 링크 또는 잘못된 경로를 확인하세요.')


def download_verified(path,url,sha):
    """Resume partial bytes only at a validated offset; publish after pinned SHA."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.part')
    regular(path);regular(temp)
    if path.exists() and digest(path)==sha:return
    offset=temp.stat().st_size if temp.exists() else 0
    if offset and digest(temp)==sha:temp.replace(path);return
    request=urllib.request.Request(url,headers={'Range':f'bytes={offset}-'} if offset else {})
    print(('Resuming ' if offset else 'Downloading ')+path.name,flush=True)
    try:response=urllib.request.urlopen(request,timeout=60)
    except urllib.error.HTTPError as error:
        if error.code==416 and offset:
            match=re.fullmatch(r'bytes \*/(\d+)',error.headers.get('Content-Range',''))
            if match and offset>=int(match[1]):
                error.close();temp.unlink();return download_verified(path,url,sha)
        error.close();raise
    with response:
        status=response.status
        complete=True
        if status==206:
            match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
            if not match or int(match[1])!=offset or int(match[2])<offset or int(match[3])<=int(match[2]):
                raise ValueError('잘못된 다운로드 범위입니다. 기존 파일을 보존했습니다.')
            expected=int(match[2])-offset+1
            complete=int(match[2])+1==int(match[3])
        elif status==200:
            offset=0;length=response.headers.get('Content-Length')
            expected=int(length) if length is not None and length.isdecimal() else None
        else:raise ValueError(f'지원하지 않는 다운로드 응답: {status}')
        received=0;announced=0
        with temp.open('ab' if offset else 'wb') as output:
            while chunk:=response.read(1024*1024):
                if expected is not None and received+len(chunk)>expected:raise ValueError('다운로드 크기가 응답 범위를 초과했습니다.')
                output.write(chunk);received+=len(chunk)
                if received-announced>=100*1024*1024:
                    print(f'{(offset+received)//1024//1024} MiB downloaded',flush=True);announced=received
        if expected is not None and received!=expected:raise ValueError('다운로드가 중단되었습니다. 같은 명령으로 이어받으세요.')
        if not complete:raise ValueError('일부 범위를 받았습니다. 같은 명령으로 이어받으세요.')
    if digest(temp)!=sha:
        # An invalid complete partial must not poison every subsequent resume.
        temp.unlink()
        raise RuntimeError('SHA-256 mismatch: '+path.name+'; 다시 실행하면 새로 다운로드합니다.')
    regular(path);temp.replace(path)


def extract_runtime(archive_path,dest):
    """Stage each member beside its destination; preserve matching loaded DLLs."""
    dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        entries=archive.infolist();seen=set()
        for entry in entries:
            target=dest/entry.filename
            if '\\' in entry.filename or ':' in entry.filename or not target.resolve().is_relative_to(dest.resolve()):raise ValueError('Invalid archive path')
            folded=entry.filename.casefold()
            if folded in seen:raise ValueError('Duplicate archive path')
            seen.add(folded)
            for parent in (target,*target.parents):
                if parent==dest.parent:break
                if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():raise ValueError('Linked runtime path')
            if not entry.is_dir():regular(target)
        for entry in entries:
            target=dest/entry.filename
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                staged=Path(temporary)/'member'
                with archive.open(entry) as source,staged.open('wb') as output:shutil.copyfileobj(source,output,64*1024)
                if target.exists() and digest(target)==digest(staged):continue
                regular(target);staged.replace(target)


def main():
    (ROOT/'runtime').mkdir(exist_ok=True)
    try:lock=acquire(ROOT/'runtime'/'model-install.lock')
    except OSError:raise RuntimeError('다른 모델 설치가 진행 중이거나 설치 폴더를 사용할 수 없습니다.') from None
    with lock:install()


def install():
    installed=[]
    for name,url,sha in ARTIFACTS:
        path=ROOT/name
        download_verified(path,url,sha)
        print('Verified '+name,flush=True)
        installed.append({'path':name,'source':url,'sha256':sha,'bytes':path.stat().st_size})
    extract_runtime(ROOT/ARTIFACTS[0][0],ROOT/'runtime'/'llama')
    manifest=ROOT/'models'/'installed.json';regular(manifest)
    with tempfile.TemporaryDirectory(dir=manifest.parent) as temporary:
        staged=Path(temporary)/'installed.json';staged.write_text(json.dumps(installed,indent=2),encoding='utf-8');staged.replace(manifest)
    print('Local model installation complete.',flush=True)

if __name__=='__main__':main()
