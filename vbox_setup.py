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
import threading
from contextlib import contextmanager
from environment_profiles import recommend,validate,identity
from setup_config import read_setting,write_setting
from vm_runtime import BusyVM

ROOT=Path(__file__).resolve().parent
_PROJECT_MUTEX=threading.Lock()

@contextmanager
def project_lock():
    if not _PROJECT_MUTEX.acquire(blocking=False):raise BusyVM('이 프로젝트의 다른 VirtualBox 준비·복구가 진행 중입니다.')
    handle=None;locked=False
    try:
        folder=ROOT/'runtime';path=folder/'vbox-setup.lock'
        if folder.resolve()!=ROOT.resolve()/'runtime':raise ValueError('runtime 저장 경로를 확인하세요.')
        folder.mkdir(exist_ok=True)
        if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_nlink!=1):raise ValueError('프로젝트 준비 잠금 파일을 확인하세요.')
        handle=path.open('a+b')
        if os.fstat(handle.fileno()).st_size==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError:raise BusyVM('다른 데이터 폴더의 실행기가 이 프로젝트를 준비·복구 중입니다.') from None
        yield
    finally:
        try:
            if handle:
                try:
                    if locked and os.name=='nt':
                        import msvcrt
                        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                finally:handle.close()
        finally:_PROJECT_MUTEX.release()

def project_prefix(owner):return 'moa-shared-'+owner.replace('-','')+'-'

def registered_vms(value):
    rows=[]
    for line in value.splitlines():
        if not line.strip():continue
        match=re.fullmatch(r'"(.*)" \{([0-9a-fA-F-]{36})\}',line)
        if not match:raise ValueError('등록 목록 형식을 확인하지 못했습니다. 비어 있다고 가정하지 않습니다.')
        rows.append({'name':match[1],'id':str(uuid.UUID(match[2]))})
    return rows

def runtime_owner(create=False):
    folder=ROOT/'runtime';marker=folder/'vbox-owner.id'
    if folder.resolve()!=ROOT.resolve()/'runtime':raise ValueError('runtime 저장 경로를 확인하세요.')
    if not marker.exists() and not marker.is_symlink():
        if not create:return None
        folder.mkdir(exist_ok=True)
        with marker.open('x',encoding='ascii') as stream:stream.write(str(uuid.uuid4()))
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_nlink!=1:raise ValueError('runtime 소유 표식 형식을 확인하세요.')
    with marker.open(encoding='ascii') as stream:value=stream.read(65)
    if str(uuid.UUID(value))!=value:raise ValueError('runtime 소유 표식이 손상되었습니다.')
    return value

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
    owner=runtime_owner()
    if owner is None:raise ValueError('원래 runtime 소유 표식을 확인하세요.')
    folder=ROOT/'runtime/vbox';name=project_prefix(owner)+uid[:8];disk=folder/name/'system.vdi'
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
    return {'id':uid,'root':str(ROOT.resolve()),'host':identity(),'runtime_owner':runtime_owner(),'request':clean_request,'name':name,'target_os':target,'preset':preset,
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
                registered=registered_vms(command(binary,['list','vms']))
                if any(row['id']==plan['id'] or row['name'].startswith(project_prefix(plan['runtime_owner'])) for row in registered):issues.append('이 프로젝트의 VM 등록이 이미 있습니다. 기존 VM을 보존하고 준비를 중단합니다.')
        except ValueError as error:issues.append(str(error))
    target=ROOT/'runtime/vbox'/plan['name']
    if target.exists() or target.is_symlink():issues.append('같은 VM 저장 폴더가 이미 있습니다. 덮어쓰지 않습니다.')
    try:
        if any((ROOT/'runtime/vbox').iterdir()):issues.append('프로젝트 VM 저장 영역에 기존 파일이 있습니다. 기존 준비·복원 상태를 확인하세요.')
    except FileNotFoundError:pass
    except OSError:issues.append('프로젝트 VM 저장 영역을 확인하지 못했습니다.')
    try:
        free=shutil.disk_usage(ROOT).free
        if free<(plan['disk_mb']+4096)*1024**2:issues.append('가상 디스크 용량과 호스트 여유 4GB를 위한 저장 공간이 부족합니다.')
    except OSError:issues.append('디스크 여유 공간을 확인하지 못했습니다.')
    return {'binary':binary,'version':version,'issues':issues,'can_prepare':not issues}

def preview(store,vm,request):
    with vm.exclusive(),project_lock():
        previous=read_setting(store,'vbox_preparation',{})
        if not isinstance(previous,dict):raise ValueError('VirtualBox 준비 기록 형식을 확인하세요.')
        if previous:raise ValueError('기존 VirtualBox 준비 기록이 있습니다. 파일과 기록을 확인한 뒤 별도 복구가 필요합니다.')
        runtime_owner(create=True)
        plan=build(request,str(uuid.uuid4()));plan.update(inspect(plan));plan['created']=time.time()
        write_setting(store,'vbox_plan',plan)
        return plan

def prepare(store,vm,plan_id):
    with vm.exclusive(),project_lock():
        plan=read_setting(store,'vbox_plan',{})
        if not isinstance(plan,dict):raise ValueError('VirtualBox 준비 계획 형식을 확인하세요.')
        if not isinstance(plan_id,str) or plan_id!=plan.get('id'):raise ValueError('설치 준비 내용을 다시 확인하세요.')
        if not plan.get('runtime_owner') or runtime_owner()!=plan['runtime_owner']:raise ValueError('원래 runtime 소유 표식을 확인하지 못했습니다. 준비 내용을 다시 확인하세요.')
        previous=read_setting(store,'vbox_preparation',{})
        if not isinstance(previous,dict):raise ValueError('VirtualBox 준비 기록 형식을 확인하세요.')
        if previous:
            if previous.get('root')!=str(ROOT.resolve()) or previous.get('host')!=identity():raise ValueError('다른 프로젝트·호스트의 준비 기록입니다. 기존 VM을 자동 연결하지 않습니다.')
            owner=runtime_owner()
            if owner is None or previous.get('runtime_owner')!=owner or plan.get('runtime_owner')!=owner:raise ValueError('원래 runtime 소유 표식이 없습니다. 준비 기록을 자동 적용하지 않습니다.')
            if previous.get('id')==plan_id and previous.get('state')=='prepared':return previous
            raise ValueError('이전 준비가 완료되지 않았거나 다른 준비 기록이 있습니다. 자동 재실행하지 않습니다.')
        fresh=build(plan['request'],plan_id)
        for key in ('root','host','runtime_owner','commands','iso_stamp'):
            if fresh[key]!=plan.get(key):raise ValueError('호스트·ISO·추천 자원이 바뀌었습니다. 설치 준비 내용을 다시 확인하세요.')
        if vm.session():raise ValueError('기존 QEMU VM 기록이 있습니다. VM과 공유 사용권 상태를 확인하세요.')
        readiness=inspect(plan)
        if not readiness['can_prepare'] or readiness['binary']!=plan.get('binary') or readiness['version']!=plan.get('version'):raise ValueError('VirtualBox 설치 상태나 저장 공간이 바뀌었습니다. 준비 내용을 다시 확인하세요.')
        record={'id':plan_id,'name':plan['name'],'root':plan['root'],'host':plan['host'],'runtime_owner':plan['runtime_owner'],'state':'preparing','completed_steps':0,'scope':plan['scope']}
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

def _recovery_state(store):
    with store.connect() as c:
        rows={row['key']:row['value'] for row in c.execute("SELECT key,value FROM settings WHERE key IN ('vbox_plan','vbox_preparation')")}
    return rows

def _check_recovery(rows):
    result={'record_id':None,'name':None,'can_clear':False,'registered':None,'folder_present':None,
            'scope':'준비 메타데이터만 정리합니다. VM 등록·파일·전원은 변경하지 않습니다.'}
    try:
        record=json.loads(rows.get('vbox_preparation','{}'));plan=json.loads(rows.get('vbox_plan','{}'))
        if not record:return {**result,'reason':'정리할 준비 기록이 없습니다.'}
        if not isinstance(record,dict) or not isinstance(plan,dict):raise ValueError('준비 기록 형식 오류')
        uid=record.get('id');valid=str(uuid.UUID(uid))
        if uid!=valid:raise ValueError('준비 ID 오류')
        owner=runtime_owner()
        if owner is None:raise ValueError('원래 runtime 소유 표식이 없습니다. 복원 기록을 자동 정리하지 않습니다.')
        name=project_prefix(owner)+uid[:8];result.update(record_id=uid,name=name)
        for value in (record,plan):
            if value.get('id')!=uid or value.get('name')!=name or value.get('root')!=str(ROOT.resolve()) or value.get('host')!=identity():raise ValueError('다른 프로젝트·호스트이거나 손상된 준비 기록입니다. 자동 정리하지 않습니다.')
            if value.get('runtime_owner')!=owner:raise ValueError('runtime 소유 표식이 일치하지 않습니다. 자동 정리하지 않습니다.')
        if record.get('state') not in ('preparing','prepared','interrupted'):raise ValueError('준비 상태를 확인하지 못했습니다.')
        folder=ROOT/'runtime/vbox'
        if folder.resolve()!=ROOT.resolve()/'runtime/vbox':raise ValueError('VM 저장 폴더가 프로젝트 밖을 가리킵니다.')
        target=folder/name
        try:target.lstat();present=True
        except FileNotFoundError:present=False
        result['folder_present']=present
        binary=executable()
        if not binary:raise ValueError('VirtualBox 도구가 없어 등록 여부를 확인하지 못했습니다. 기록을 보존합니다.')
        version=command(binary,['--version'])
        if not re.fullmatch(r'7\.2\.\d+r\d+(?:_.*)?',version):raise ValueError('지원 버전의 등록 목록을 확인해야 합니다.')
        ids=[row['id'] for row in registered_vms(command(binary,['list','vms']))]
        result['registered']=uid in ids
        # Recheck the filesystem after the metadata query; never remove VM data.
        try:target.lstat();result['folder_present']=True
        except FileNotFoundError:pass
        if result['registered'] or result['folder_present']:raise ValueError('VM 등록 또는 저장 폴더가 남아 있습니다. VirtualBox에서 해당 VM과 파일을 직접 확인하세요.')
        result.update(can_clear=True,reason='조회 시점에 해당 UUID의 등록과 전용 폴더가 없습니다. 준비 기록만 정리할 수 있습니다.')
    except (OSError,ValueError,TypeError,AttributeError) as error:
        result['reason']=str(error) if isinstance(error,ValueError) else '준비 기록·파일 상태를 확인하지 못했습니다. 기록을 보존합니다.'
    return result

def recovery_check(store,vm):
    with vm.exclusive(),project_lock():return _check_recovery(_recovery_state(store))

def clear_preparation(store,vm,record_id,request_key):
    if not isinstance(record_id,str) or not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',request_key):raise ValueError('정리할 준비 ID와 요청 ID를 확인하세요.')
    with vm.exclusive(),project_lock():
        with store.connect() as c:
            table=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vbox_recovery_audit'").fetchone()
            receipt=c.execute('SELECT record_id FROM vbox_recovery_audit WHERE request_key=?',(request_key,)).fetchone() if table else None
        if receipt:
            if receipt['record_id']!=record_id:raise ValueError('같은 요청 ID에 다른 준비 기록이 전달되었습니다.')
            return {'cleared':True,'record_id':record_id,'replayed':True}
        before=_recovery_state(store);check=_check_recovery(before)
        if not check['can_clear'] or check['record_id']!=record_id:raise ValueError(check['reason'])
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current={row['key']:row['value'] for row in c.execute("SELECT key,value FROM settings WHERE key IN ('vbox_plan','vbox_preparation')")}
            if current!=before:raise ValueError('확인 중 준비 기록이 바뀌었습니다. 다시 확인하세요.')
            c.execute('CREATE TABLE IF NOT EXISTS vbox_recovery_audit(request_key TEXT PRIMARY KEY,record_id TEXT NOT NULL,snapshot TEXT NOT NULL,created REAL NOT NULL)')
            c.execute('INSERT INTO vbox_recovery_audit VALUES(?,?,?,?)',(request_key,record_id,json.dumps(before,ensure_ascii=False),time.time()))
            c.execute("DELETE FROM settings WHERE key IN ('vbox_plan','vbox_preparation')")
        return {'cleared':True,'record_id':record_id,'replayed':False}
