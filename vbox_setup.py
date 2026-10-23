"""VirtualBox 7.2 preparation: project-owned files only; never boot a guest."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
import uuid
from environment_profiles import recommend,validate,identity
from setup_config import read_setting,write_setting

ROOT=Path(__file__).resolve().parent

def executable():
    override=os.environ.get('MOA_VBOXMANAGE')
    if override:
        path=Path(override)
        if not path.is_absolute() or path.name.lower() not in ('vboxmanage','vboxmanage.exe') or not path.is_file():raise ValueError('MOA_VBOXMANAGE에는 실제 VBoxManage 실행 파일의 절대 경로를 지정하세요.')
        return str(path.resolve())
    system=platform.system()
    candidates=([Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Oracle/VirtualBox/VBoxManage.exe'] if system=='Windows' else
                [Path('/Applications/VirtualBox.app/Contents/MacOS/VBoxManage')] if system=='Darwin' else
                [Path('/usr/bin/VBoxManage'),Path('/usr/local/bin/VBoxManage')])
    return next((str(p.resolve()) for p in candidates if p.is_file()),None)

def command(binary,args):
    try:result=subprocess.run([binary,*args],capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.SubprocessError):raise ValueError('VirtualBox 명령을 완료하지 못했습니다. 설치 상태와 준비 기록을 확인하세요.') from None
    if result.returncode:raise ValueError('VirtualBox 명령 실패: '+args[0]+'. 기존 파일을 보존했습니다.')
    if len(result.stdout)>1024*1024:raise ValueError('VirtualBox 응답 크기 제한을 초과했습니다.')
    return result.stdout.decode('utf-8',errors='replace').strip()

def checked_iso(value):
    if not isinstance(value,str) or not value or len(value)>1000:raise ValueError('프로젝트 내부 ISO 경로를 입력하세요.')
    path=ROOT/value
    resolved=path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file() or resolved.suffix.lower()!='.iso':raise ValueError('프로젝트 내부의 실제 ISO 파일을 지정하세요.')
    return resolved

def build(request,uid):
    if not isinstance(request,dict):raise ValueError('VirtualBox 준비 설정 객체가 필요합니다.')
    if str(uuid.UUID(uid))!=uid:raise ValueError('VirtualBox 준비 ID 오류')
    profile=validate(request.get('profile',{}));suggestion=recommend(profile)
    if profile['location']!='shared_vm':raise ValueError('공유 VM 위치를 선택하세요.')
    target=suggestion['target_os']
    if target not in ('windows','linux'):raise ValueError('이 준비 경로는 Windows·Linux x64 VM용입니다. macOS는 Mac 실행 환경을 연결하세요.')
    if identity()['architecture'].lower() not in ('amd64','x86_64'):raise ValueError('현재 준비 경로는 x64 호스트만 지원합니다. ARM 장비는 별도 연결이 필요합니다.')
    iso=checked_iso(request.get('iso'))
    preset_id=request.get('preset','auto')
    if preset_id not in ('auto','light','balanced','development'):raise ValueError('VM 프리셋 오류')
    selected=(suggestion['recommended'] or 'light') if preset_id=='auto' else preset_id
    preset=next(p for p in suggestion['presets'] if p['id']==selected)
    folder=ROOT/'runtime/vbox';name='moa-shared-'+uid[:8];disk=folder/name/'system.vdi'
    if folder.resolve()!=ROOT.resolve()/'runtime/vbox':raise ValueError('VirtualBox 저장 폴더가 프로젝트 밖을 가리킵니다.')
    disk_mb=131072 if target=='windows' else 65536
    commands=[['createvm','--name',name,'--platform-architecture','x86','--ostype','Windows11_64' if target=='windows' else 'Linux_64','--uuid',uid,'--basefolder',str(folder),'--register'],
              ['modifyvm',uid,'--memory',str(preset['memory_mb']),'--cpus',str(preset['cpus']),'--firmware','efi','--ioapic','on','--graphicscontroller','vboxsvga' if target=='windows' else 'vmsvga','--nic1','none','--clipboard-mode','disabled','--drag-and-drop','disabled'],
              ['createmedium','disk','--filename',str(disk),'--size',str(disk_mb),'--format','VDI'],
              ['storagectl',uid,'--name','SATA','--add','sata','--controller','IntelAHCI'],
              ['storageattach',uid,'--storagectl','SATA','--port','0','--device','0','--type','hdd','--medium',str(disk)],
              ['storageattach',uid,'--storagectl','SATA','--port','1','--device','0','--type','dvddrive','--medium',str(iso)]]
    if target=='windows':commands[1]+=['--tpm-type','2.0']
    stat=iso.stat()
    clean_request={'profile':profile,'iso':str(iso.relative_to(ROOT.resolve())),'preset':selected}
    return {'id':uid,'root':str(ROOT.resolve()),'host':identity(),'request':clean_request,'name':name,'target_os':target,'preset':preset,
            'disk_mb':disk_mb,'commands':commands,'iso_stamp':[stat.st_size,stat.st_mtime_ns],
            'recommendation_note':suggestion['note'],'scope':'프로젝트 VM 정의·등록·디스크·ISO 연결만 준비합니다. ISO 내용·부팅은 미검증이며 OS 설치·입력·화면 기록은 실행하지 않습니다.'}

def inspect(plan):
    binary=executable();issues=[];version=None
    if not binary:issues.append('기본 설치 경로에서 VBoxManage를 찾지 못했습니다. 설치 위치를 확인하거나 MOA_VBOXMANAGE 절대 경로를 지정하세요. 도구·드라이버를 자동 설치하지 않습니다.')
    else:
        try:
            version=command(binary,['--version'])
            if not re.fullmatch(r'7\.2\.\d+r\d+(?:_.*)?',version):issues.append('이 준비 경로는 VirtualBox 7.2 명령 형식을 지원합니다.')
            else:
                registered=command(binary,['list','vms'])
                if plan['id'] in registered or '"'+plan['name']+'"' in registered:issues.append('같은 이름이나 UUID가 등록돼 있습니다. 기존 VM을 보존하고 준비를 중단합니다.')
        except ValueError as error:issues.append(str(error))
    target=ROOT/'runtime/vbox'/plan['name']
    if target.exists() or target.is_symlink():issues.append('같은 VM 저장 폴더가 이미 있습니다. 덮어쓰지 않습니다.')
    try:
        free=shutil.disk_usage(ROOT).free
        if free<(plan['disk_mb']+4096)*1024**2:issues.append('가상 디스크 용량과 호스트 여유 4GB를 위한 저장 공간이 부족합니다.')
    except OSError:issues.append('디스크 여유 공간을 확인하지 못했습니다.')
    return {'binary':binary,'version':version,'issues':issues,'can_prepare':not issues}

def preview(store,vm,request):
    with vm.exclusive():
        previous=read_setting(store,'vbox_preparation',{})
        if not isinstance(previous,dict):raise ValueError('VirtualBox 준비 기록 형식을 확인하세요.')
        if previous:raise ValueError('기존 VirtualBox 준비 기록이 있습니다. 파일과 기록을 확인한 뒤 별도 복구가 필요합니다.')
        plan=build(request,str(uuid.uuid4()));plan.update(inspect(plan));plan['created']=time.time()
        write_setting(store,'vbox_plan',plan)
        return plan

def prepare(store,vm,plan_id):
    with vm.exclusive():
        plan=read_setting(store,'vbox_plan',{})
        if not isinstance(plan,dict):raise ValueError('VirtualBox 준비 계획 형식을 확인하세요.')
        if not isinstance(plan_id,str) or plan_id!=plan.get('id'):raise ValueError('설치 준비 내용을 다시 확인하세요.')
        previous=read_setting(store,'vbox_preparation',{})
        if not isinstance(previous,dict):raise ValueError('VirtualBox 준비 기록 형식을 확인하세요.')
        if previous:
            if previous.get('root')!=str(ROOT.resolve()) or previous.get('host')!=identity():raise ValueError('다른 프로젝트·호스트의 준비 기록입니다. 기존 VM을 자동 연결하지 않습니다.')
            if previous.get('id')==plan_id and previous.get('state')=='prepared':return previous
            raise ValueError('이전 준비가 완료되지 않았거나 다른 준비 기록이 있습니다. 자동 재실행하지 않습니다.')
        fresh=build(plan['request'],plan_id)
        for key in ('root','host','commands','iso_stamp'):
            if fresh[key]!=plan.get(key):raise ValueError('호스트·ISO·추천 자원이 바뀌었습니다. 설치 준비 내용을 다시 확인하세요.')
        if vm.session():raise ValueError('기존 QEMU VM 기록이 있습니다. VM과 공유 사용권 상태를 확인하세요.')
        readiness=inspect(plan)
        if not readiness['can_prepare'] or readiness['binary']!=plan.get('binary') or readiness['version']!=plan.get('version'):raise ValueError('VirtualBox 설치 상태나 저장 공간이 바뀌었습니다. 준비 내용을 다시 확인하세요.')
        record={'id':plan_id,'name':plan['name'],'root':plan['root'],'host':plan['host'],'state':'preparing','completed_steps':0,'scope':plan['scope']}
        write_setting(store,'vbox_preparation',record)
        try:
            for index,args in enumerate(plan['commands']):
                command(readiness['binary'],args)
                record['completed_steps']=index+1;write_setting(store,'vbox_preparation',record)
            record['state']='prepared';write_setting(store,'vbox_preparation',record)
        except (OSError,ValueError,RuntimeError):
            record['state']='interrupted'
            try:write_setting(store,'vbox_preparation',record)
            except (OSError,RuntimeError):pass
            raise
        return record
