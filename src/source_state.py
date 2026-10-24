"""Compare startup project source fingerprints; never reload or restart anything."""
import hashlib
import json
from pathlib import Path
import os
import stat
import sys
import threading
import time

LIMIT=2*1024*1024


def loaded_sources(root):
    root=Path(root).resolve();names=set()
    for module in tuple(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if not isinstance(filename,str):continue
        path=Path(filename).absolute()
        if path.parent==root and path.suffix=='.py' and not path.name.startswith(('test_','verify_')):names.add(path.name)
    return sorted(names)


class SourceState:
    def __init__(self,root,files=None,clock=time.monotonic,ttl=5,config_root=None):
        self.config_root=Path(config_root or root).resolve();self.root=Path(root).resolve();self.clock=clock;self.ttl=ttl;self.lock=threading.Lock()
        self.files=sorted(set(loaded_sources(self.root) if files is None else files)|{'config.json'})
        if any(not isinstance(name,str) or Path(name).name!=name or not (name.endswith('.py') or name=='config.json') for name in self.files):raise ValueError('프로젝트 소스 파일명만 확인할 수 있습니다.')
        self.baseline=self._snapshot();self.cached=None;self.expiry=0

    def _read(self,name):
        path=(self.config_root if name=='config.json' else self.root)/name
        try:
            before=path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or getattr(before,'st_file_attributes',0)&0x400 or before.st_size>LIMIT:return {'kind':'unknown'}
            digest=hashlib.sha256();size=0;config_parts=[]
            with path.open('rb') as stream:
                opened=os.fstat(stream.fileno())
                if (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino) or opened.st_nlink!=1:return {'kind':'unknown'}
                while block:=stream.read(65536):
                    size+=len(block)
                    if size>LIMIT:return {'kind':'unknown'}
                    digest.update(block)
                    if name=='config.json':config_parts.append(block)
            after=path.lstat()
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or after.st_nlink!=1:return {'kind':'unknown'}
            if name=='config.json':
                try:
                    value=json.loads(b''.join(config_parts))
                    if not isinstance(value,dict):return {'kind':'unknown'}
                    digest=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())
                except (ValueError,UnicodeError,RecursionError):return {'kind':'unknown'}
            return {'kind':'ok','sha256':digest.hexdigest()}
        except FileNotFoundError:return {'kind':'missing'}
        except OSError:return {'kind':'unknown'}

    def _snapshot(self):return {name:self._read(name) for name in self.files}

    @staticmethod
    def _identity(snapshot):return hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def inspect(self,force=False):
        with self.lock:
            if not force and self.cached is not None and self.clock()<self.expiry:return {**self.cached,'changed_files':list(self.cached['changed_files']),'unknown_files':list(self.cached['unknown_files'])}
            current=self._snapshot()
            unknown=[name for name in self.files if any(values[name]['kind']=='unknown' or values[name]['kind']=='missing' and name!='config.json' for values in (self.baseline,current))]
            changed=[name for name in self.files if current[name]!=self.baseline[name]]
            state='unknown' if unknown else 'changed' if changed else 'matching'
            self.cached={'state':state,'restart_required':True if state=='changed' else False if state=='matching' else None,
                         'running_id':self._identity(self.baseline),'current_id':None if unknown else self._identity(current),
                         'changed_files':changed,'unknown_files':unknown,'checked_at':time.time(),
                         'scope':'앱 시작 때 로드된 프로젝트 Python 소스와 config.json의 파일 지문 비교입니다. 화면 파일·외부 패키지·실제 기능 실행 검증은 포함하지 않습니다.'}
            self.expiry=self.clock()+self.ttl
            return {**self.cached,'changed_files':list(changed),'unknown_files':list(unknown)}


def diagnostic_check(value):
    state=value['state']
    detail={'matching':'서버 시작 때의 소스와 현재 파일이 같습니다.','changed':'서버 시작 뒤 파일이 바뀌었습니다. 작업을 마친 뒤 정상 종료·시작하면 적용됩니다. 화면 새로고침만으로는 적용되지 않습니다.','unknown':'일부 소스를 확인하지 못했습니다. 파일과 권한을 확인한 뒤 다시 진단하세요.'}[state]
    return {'id':'application_sources','label':'서버 변경 적용','state':{'matching':'ready','changed':'restart','unknown':'error'}[state],'detail':detail}
