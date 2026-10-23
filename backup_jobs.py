"""Durable backup request identity and safe helper scheduling."""
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from vm_runtime import pid_alive


def _folder(root):
    root=Path(root).resolve(); folder=root/'backups'
    if folder.resolve()!=folder:raise ValueError('프로젝트 내부 backups 폴더를 사용하세요.')
    folder.mkdir(exist_ok=True)
    return folder


def _key(key):
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',key):
        raise ValueError('백업 요청 ID가 올바르지 않습니다.')
    return key


def request_status(store, root, key):
    key=_key(key)
    with store.connect() as c:
        row=c.execute('SELECT * FROM backup_requests WHERE request_key=?',(key,)).fetchone()
    if not row:return None
    result=dict(row); folder=_folder(root)
    if result['file']!='workspace-'+hashlib.sha256(key.encode()).hexdigest()+'.zip':raise ValueError('백업 요청 기록의 파일명이 올바르지 않습니다.')
    status=folder/Path(result['file']).with_suffix('.status.json')
    try:
        if not status.is_symlink() and status.resolve().parent==folder and status.stat().st_size<=4096:
            value=json.loads(status.read_text(encoding='utf-8'))
            if isinstance(value,dict) and value.get('state') in ('waiting','completed','failed'):
                result['state']=value['state'];result['message']=str(value.get('message',''))[:300]
                if type(value.get('helper_pid')) is int:result['helper_pid']=value['helper_pid']
    except (OSError,ValueError):pass
    result.setdefault('message','앱 종료 후 백업을 준비하고 있습니다.')
    if result['state']=='completed' and not (folder/result['file']).is_file():
        result['state']='missing';result['message']='완료 기록은 있지만 백업 ZIP이 없습니다. 보관 위치를 확인하거나 다시 백업하세요.'
    if result['state'] in ('prepared','waiting') and not pid_alive(result['helper_pid']):
        result['state']='failed';result['message']='백업 도우미가 실행 중이 아닙니다. 같은 요청으로 다시 시도하세요.'
        if (folder/result['file']).is_file():
            result['state']='existing';result['message']='백업 ZIP은 있지만 완료 상태를 확인하지 못했습니다. 백업 목록에서 복원 전 검사를 실행하세요.'
    return result


def schedule_request(store, root, key, parent_pid, launch=None):
    key=_key(key);folder=_folder(root)
    filename='workspace-'+hashlib.sha256(key.encode()).hexdigest()+'.zip'
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR IGNORE INTO backup_requests(request_key,file,state,helper_pid,created) VALUES(?,?,?,?,?)',(key,filename,'prepared',None,time.time()))
        row=dict(c.execute('SELECT * FROM backup_requests WHERE request_key=?',(key,)).fetchone())
        status=folder/Path(filename).with_suffix('.status.json')
        if status.is_file():
            try:
                value=json.loads(status.read_text(encoding='utf-8'))
                if isinstance(value,dict) and value.get('state')=='completed' and (folder/filename).is_file():
                    c.execute("UPDATE backup_requests SET state='completed' WHERE request_key=?",(key,))
                    return {'request_key':key,'file':filename,'state':'completed','message':'이미 완료한 백업입니다.'}
                if isinstance(value,dict) and value.get('state')=='waiting' and pid_alive(value.get('helper_pid')):
                    return {'request_key':key,'file':filename,'state':'waiting','message':'접수된 백업입니다. 앱 종료 후 진행합니다.'}
            except (OSError,ValueError):pass
        if row['helper_pid'] and pid_alive(row['helper_pid']):
            return {'request_key':key,'file':filename,'state':'waiting','message':'접수된 백업입니다. 앱 종료 후 진행합니다.'}
        if (folder/filename).exists():
            return {'request_key':key,'file':filename,'state':'existing','message':'기존 ZIP을 보존했습니다. 백업 목록에서 복원 전 검사를 실행하세요. 새 백업은 버튼을 다시 눌러 요청할 수 있습니다.'}
        process=(launch or subprocess.Popen)([sys.executable,str(Path(root)/'data_backup.py'),'scheduled',str(store.root),str(folder/filename),'--wait-pid',str(parent_pid)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        c.execute("UPDATE backup_requests SET state='waiting',helper_pid=? WHERE request_key=?",(process.pid,key))
    return {'request_key':key,'file':filename,'state':'waiting','message':'앱 종료 후 backups 폴더에 저장합니다. 백업 후 Start.ps1로 다시 실행하세요.'}
