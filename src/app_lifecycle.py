"""Project-owned app registration and file-based stop requests; no HTTP control."""
import json
import os
from pathlib import Path
import re
import time


def runtime_folder(root):
    root=Path(root).resolve();folder=root/'runtime'
    if folder.resolve()!=folder:raise ValueError('실행 기록 경로가 프로젝트 밖을 가리킵니다.')
    folder.mkdir(exist_ok=True)
    return root,folder


def registry_path(root,port):
    if type(port) is not int or not 1<=port<=65535:raise ValueError('포트는 1~65535 정수여야 합니다.')
    root,folder=runtime_folder(root)
    return root,folder,folder/('app-'+str(port)+'.json')


def regular(path):
    return not path.is_symlink() and path.is_file() and path.stat().st_nlink==1


def read_registration(root,port):
    root,folder,path=registry_path(root,port)
    if not regular(path) or path.stat().st_size>65536:raise ValueError('이 프로젝트의 앱 실행 기록을 확인하지 못했습니다.')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1 or value.get('root')!=str(root) or type(value.get('port')) is not int or value['port']!=port or type(value.get('pid')) is not int or value['pid']<1 or not isinstance(value.get('instance'),str) or not re.fullmatch('[0-9a-f]{32}',value['instance']) or value.get('stop_file')!='app-stop-'+value['instance']+'.request':raise ValueError('앱 실행 기록의 소유 정보가 일치하지 않습니다.')
    return value


def register(root,port,instance):
    root,folder,path=registry_path(root,port)
    if not isinstance(instance,str) or not re.fullmatch('[0-9a-f]{32}',instance):raise ValueError('앱 인스턴스 ID 오류')
    if path.exists() and not regular(path) or path.is_symlink():raise ValueError('앱 실행 기록 파일을 확인하세요.')
    value={'version':1,'root':str(root),'port':port,'pid':os.getpid(),'instance':instance,'stop_file':'app-stop-'+instance+'.request','created':time.time()}
    temporary=folder/('.app-'+str(port)+'-'+instance+'.tmp');created=False
    try:
        with temporary.open('x',encoding='utf-8') as stream:created=True;json.dump(value,stream,ensure_ascii=False)
        temporary.replace(path)
    finally:
        if created:temporary.unlink(missing_ok=True)
    return value


def create_request(path):
    try:
        with Path(path).open('xb'):pass
    except FileExistsError:
        if not regular(Path(path)) or Path(path).stat().st_size:raise ValueError('종료 요청 파일 형식 오류')


def request_stop(root,port,expected_pid=None):
    value=read_registration(root,port)
    if expected_pid is not None and value['pid']!=expected_pid:raise ValueError('감독이 시작한 앱의 실행 기록이 아닙니다.')
    _,folder,_=registry_path(root,port);create_request(folder/value['stop_file'])
    return value


def stop_requested(root,value):
    _,folder,_=registry_path(root,value['port']);path=folder/value['stop_file']
    if not path.exists() and not path.is_symlink():return False
    if not regular(path) or path.stat().st_size:raise ValueError('종료 요청 파일 형식 오류')
    return True


def unregister(root,value):
    _,folder,path=registry_path(root,value['port'])
    try:
        if read_registration(root,value['port'])['instance']==value['instance']:path.unlink()
    except (OSError,ValueError):pass
    marker=folder/value['stop_file']
    if regular(marker):marker.unlink(missing_ok=True)
