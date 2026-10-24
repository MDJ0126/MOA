"""Install project dependencies and retire only superseded package metadata."""
from email.parser import Parser
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent


def prune_metadata(target,report):
    target=Path(target).resolve()
    normalized=lambda name:re.sub(r'[-_.]+','-',name).lower()
    versions={normalized(item['metadata']['name']):item['metadata']['version'] for item in report['install']}
    obsolete=[]
    for folder in target.glob('*.dist-info'):
        info=folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400 or folder.resolve().parent!=target:
            raise ValueError('연결 도구 메타데이터 경로를 확인하세요.')
        metadata=folder/'METADATA';info=metadata.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('연결 도구 메타데이터 파일을 확인하세요.')
        fields=Parser().parsestr(metadata.read_text(encoding='utf-8'))
        name=normalized(fields.get('Name',''))
        if name not in versions or fields.get('Version')==versions[name]:continue
        # Validate the entire obsolete metadata tree before removing anything.
        for entry in folder.rglob('*'):
            info=entry.lstat()
            if entry.is_symlink() or getattr(info,'st_file_attributes',0)&0x400 or folder not in entry.resolve().parents or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1):
                raise ValueError('이전 연결 도구 메타데이터에 링크가 있습니다.')
        obsolete.append(folder)
    for folder in obsolete:shutil.rmtree(folder)


def install(arguments):
    runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True)
    target=runtime/'pythonlibs'
    if runtime.resolve()!=runtime or target.resolve()!=target:
        raise ValueError('프로젝트 내부의 독립된 연결 도구 폴더를 사용하세요.')
    with tempfile.TemporaryDirectory(prefix='dependency-',dir=runtime) as temporary:
        report=Path(temporary)/'install.json'
        result=subprocess.run([sys.executable,'-m','pip','install','--upgrade','--target',str(target),'--report',str(report),*arguments])
        if result.returncode:return result.returncode
        prune_metadata(target,json.loads(report.read_text(encoding='utf-8')))
    return 0


if __name__=='__main__':raise SystemExit(install(sys.argv[1:]))
