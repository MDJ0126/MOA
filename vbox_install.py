"""Prepare unattended media for an owned, powered-off VM; never start it."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import vbox_setup as vbox
from setup_config import read_setting,write_setting
from private_files import private_directory

ROOT=Path(__file__).resolve().parent

def quiet_command(binary,args):
    try:result=subprocess.run([binary,*args],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.SubprocessError):raise ValueError('설치 파일 준비 명령을 완료하지 못했습니다. 기록을 확인하세요.') from None
    if result.returncode:raise ValueError('설치 파일 준비 명령이 실패했습니다. 자동 재실행하지 않습니다.')

def off_configuration(value,plan):
    fields={}
    for line in value.splitlines():
        key,sep,raw=line.partition('=')
        if key not in ('VMState','UUID','CfgFile'):continue
        if not sep or key in fields:raise ValueError('VM 상태 조회 형식 오류')
        try:fields[key]=json.loads(raw)
        except ValueError:raise ValueError('VM 상태 조회 값을 확인하지 못했습니다.') from None
    expected=ROOT/'runtime/vbox'/plan['name']/(plan['name']+'.vbox')
    if fields.get('VMState')!='poweroff' or fields.get('UUID')!=plan['id']:raise ValueError('소유 VM의 전원이 꺼져 있어야 설치 파일을 준비할 수 있습니다.')
    if not isinstance(fields.get('CfgFile'),str) or Path(fields['CfgFile']).resolve()!=expected.resolve() or expected.parent.resolve()!=ROOT.resolve()/'runtime/vbox'/plan['name'] or expected.is_symlink() or not expected.is_file() or expected.stat().st_nlink!=1:raise ValueError('프로젝트 소유 VM 구성 파일을 확인하지 못했습니다.')

def configure(store,vm,value):
    if not isinstance(value,dict):raise ValueError('설치 설정 객체가 필요합니다.')
    key=value.get('request_key');user=value.get('username','moa');password=value.get('password');image=value.get('image_index',1)
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',key):raise ValueError('설치 요청 ID 오류')
    if not isinstance(user,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',user) or user in ('root','administrator','system','guest'):raise ValueError('VM 전용 계정 이름을 확인하세요.')
    if not isinstance(password,str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,128}',password):raise ValueError('VM 전용 비밀번호는 영문·숫자·_·- 12~128자로 입력하세요.')
    if type(image) is not int or not 1<=image<=65535:raise ValueError('설치 판본을 선택하세요.')
    with vm.exclusive(),vbox.project_lock():
        plan=read_setting(store,'vbox_plan',{});record=read_setting(store,'vbox_preparation',{})
        if not isinstance(plan,dict) or not isinstance(record,dict) or value.get('plan_id')!=plan.get('id') or record.get('state')!='prepared':raise ValueError('VM 파일 준비 완료 기록을 먼저 확인하세요.')
        owner=vbox.runtime_owner()
        for candidate in (plan,record):
            if not owner or candidate.get('runtime_owner')!=owner or candidate.get('root')!=str(ROOT.resolve()) or candidate.get('host')!=vbox.identity() or candidate.get('id')!=plan['id'] or candidate.get('name')!=plan['name']:raise ValueError('원래 프로젝트·runtime의 VM 소유 기록을 확인하지 못했습니다.')
        previous=read_setting(store,'vbox_installation',{})
        if not isinstance(previous,dict):raise ValueError('설치 준비 기록 형식을 확인하세요.')
        if previous:
            if previous.get('request_key')==key and previous.get('plan_id')==plan['id'] and previous.get('username')==user and previous.get('image_index')==image and previous.get('state')=='media_prepared':return previous
            raise ValueError('이미 설치 파일 준비 기록이 있습니다. 상태를 확인하고 자동 재실행하지 마세요.')
        binary=vbox.executable()
        if not binary or vbox.command(binary,['--version'])!=plan.get('version'):raise ValueError('검토한 VirtualBox 설치 버전을 확인하세요.')
        iso=vbox.checked_iso(plan['request']['iso']);digest,stamp=vbox.iso_fingerprint(iso)
        if digest!=plan.get('iso_sha256') or stamp!=plan.get('iso_stamp'):raise ValueError('검토한 ISO 내용이 바뀌었습니다.')
        detected=vbox.iso_metadata(vbox.command(binary,['unattended','detect','--iso='+str(iso),'--machine-readable']),plan['target_os'])
        if detected!=plan.get('iso_detection') or detected['unattended_supported'] is not True:raise ValueError('검토한 ISO의 무인 설치 지원을 확인하지 못했습니다.')
        if plan['target_os']=='windows' and image not in [entry['index'] for entry in detected['images']]:raise ValueError('ISO에서 확인한 Windows 설치 판본을 선택하세요.')
        if plan['target_os']=='linux' and image!=1:raise ValueError('Linux 기본 설치 판본을 선택하세요.')
        off_configuration(vbox.command(binary,['showvminfo',plan['id'],'--machinereadable']),plan)
        folder=ROOT/'runtime/vbox-private'
        if folder.resolve()!=ROOT.resolve()/'runtime/vbox-private':raise ValueError('설치 보호 경로 오류')
        folder.mkdir(exist_ok=True);private=private_directory(folder/str(uuid.uuid4()));credential=private/'credential.txt'
        state={'request_key':key,'plan_id':plan['id'],'vm_name':plan['name'],'username':user,'image_index':image,'state':'preparing','created':time.time(),
               'private_directory':private.relative_to(ROOT).as_posix(),'scope':'자동 설치 파일 준비 기록입니다. OS 설치·부팅·작업 실행은 미검증입니다.'}
        write_setting(store,'vbox_installation',state)
        try:
            with os.fdopen(os.open(credential,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w',encoding='utf-8',newline='\n') as stream:stream.write(password+'\n')
            quiet_command(binary,['modifyvm',plan['id'],'--os-type',detected['os_type_id']])
            args=['unattended','install',plan['id'],'--iso='+str(iso),'--user='+user,'--user-password-file='+str(credential),
                  '--auxiliary-base-path='+str(private/'install-'),'--no-install-additions','--no-install-txs','--image-index='+str(image)]
            quiet_command(binary,args)
            state['state']='media_prepared';write_setting(store,'vbox_installation',state)
        except (OSError,ValueError,RuntimeError):
            state['state']='interrupted'
            write_setting(store,'vbox_installation',state)
            raise ValueError('설치 파일 준비가 중단됐습니다. 보호 폴더와 VM 상태를 확인하세요. 자동 재실행하지 않습니다.') from None
        finally:
            try:credential.unlink(missing_ok=True)
            except OSError:
                state['credential_file_retained']=True;write_setting(store,'vbox_installation',state)
        return state
