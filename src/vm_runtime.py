"""One project-owned headless QEMU desktop with exclusive per-agent access."""
from contextlib import contextmanager,ExitStack
import json
import queue
import math
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import threading
import time
import uuid
import ctypes
import shlex
import io
import tarfile
from setup_config import read_setting,write_setting,checked_write_setting,hardware

ROOT=Path(__file__).resolve().parent.parent
DEFAULT={'enabled':False,'memory_mb':512,'cpus':1,'accelerator':'tcg','iso':'runtime/vm-downloads/moa-desktop.iso','disk':'','network':False,'guest_os':'linux'}
KEYS=set('abcdefghijklmnopqrstuvwxyz0123456789')|{'ret','esc','spc','tab','backspace','up','down','left','right','home','end','delete','shift','ctrl','alt','meta_l','minus','equal','dot','comma','slash','backslash','semicolon','apostrophe','grave','bracket_left','bracket_right'}|{f'f{i}' for i in range(1,13)}

def settings(store):
    saved=read_setting(store,'vm',None)
    if saved is None:return {**DEFAULT,'guest_os':'windows','memory_mb':4096,'cpus':2,'iso':''}
    return {**DEFAULT,**saved}

def whpx_capability():
    """Read the host capability only; never create a partition or change features."""
    if os.name!='nt':return {'available':False,'reason':'WHPX는 Windows 호스트에서만 사용할 수 있습니다.'}
    try:query=ctypes.WinDLL('WinHvPlatform.dll',winmode=0x800).WHvGetCapability
    except (OSError,AttributeError):return {'available':False,'reason':'Windows Hypervisor Platform API를 사용할 수 없습니다.'}
    query.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint,ctypes.POINTER(ctypes.c_uint)]
    query.restype=ctypes.c_long
    present=ctypes.c_int();written=ctypes.c_uint()
    result=query(0,ctypes.byref(present),ctypes.sizeof(present),ctypes.byref(written))
    if result<0 or written.value!=ctypes.sizeof(present):return {'available':None,'reason':'Windows 하이퍼바이저 상태를 확인하지 못했습니다.'}
    return {'available':bool(present.value),'reason':'Windows 하이퍼바이저 실행 확인' if present.value else 'Windows 하이퍼바이저가 실행 중이 아닙니다.'}
def same_configuration(left,right):
    return all(left.get(key,default)==right.get(key,default) for key,default in DEFAULT.items())
def validate_actions(value,pipeline):
    if not isinstance(value,list) or len(value)>40:raise ValueError('VM 화면 동작은 최대 40개입니다.')
    result=[]
    for entry in value:
        if not isinstance(entry,dict) or entry.get('role') not in pipeline:raise ValueError('VM 동작의 담당 단계를 확인하세요.')
        action=entry.get('action');clean={'role':entry['role'],'action':action}
        if action=='key':
            keys=entry.get('keys')
            if not isinstance(keys,list) or not 1<=len(keys)<=4 or any(not isinstance(k,str) or k not in KEYS for k in keys):raise ValueError('지원하는 QEMU 키 이름을 입력하세요.')
            clean['keys']=keys
        elif action=='text':
            text=entry.get('text')
            if not isinstance(text,str) or not 1<=len(text)<=160 or any(ord(char)>126 or (ord(char)<32 and char not in '\n\t') for char in text):raise ValueError('VM 텍스트 입력은 영문·숫자·기호 160자 이내입니다.')
            clean['text']=text
        elif action in ('click','drag'):
            for axis in ('x','y'):
                value=entry.get(axis)
                if not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=32767:raise ValueError('마우스 좌표는 0~32767 사이 정수입니다.')
                clean[axis]=value
            clean['button']=entry.get('button','left')
            if clean['button'] not in ('left','right','middle'):raise ValueError('마우스 버튼 오류')
            if action=='drag':
                points=entry.get('points')
                if not isinstance(points,list) or not 1<=len(points)<=60:raise ValueError('드래그 경로는 1~60개 좌표입니다.')
                clean['points']=[]
                for point in points:
                    if not isinstance(point,dict) or any(type(point.get(axis)) is not int or not 0<=point[axis]<=32767 for axis in ('x','y')):raise ValueError('드래그 좌표는 0~32767 사이 정수입니다.')
                    clean['points'].append({axis:point[axis] for axis in ('x','y')})
        elif action=='wait':
            seconds=entry.get('seconds',1)
            if not isinstance(seconds,(int,float)) or not 0<=seconds<=5:raise ValueError('대기는 동작당 0~5초입니다.')
            clean['seconds']=seconds
        elif action!='capture':raise ValueError('지원하지 않는 VM 화면 동작입니다.')
        result.append(clean)
    return result

def boot_memory(config):
    available=hardware().get('available_ram_gib');vm_gib=config['memory_mb']/1024;required=vm_gib+1
    known=type(available) in (int,float) and math.isfinite(available) and available>=0
    allowed=available>=required if known else None
    reason=(f'RAM 부족 · 필요 {required:g}GB (VM {vm_gib:g}GB + 호스트 여유 1GB) / 현재 여유 {available:.2f}GB · {required-available:.2f}GB 추가 확보 필요' if allowed is False else f'부팅 가능 · 필요 {required:g}GB / 현재 여유 {available:.2f}GB' if known else '현재 여유 RAM을 확인할 수 없습니다. 부팅 시 다시 확인합니다.')
    return {'can_start':allowed,'available_gib':available if known else None,'required_gib':required,'reason':reason,'observed_at':time.time()}

class BusyVM(ValueError):pass
class VMAdmissionBusy(BusyVM):pass
class VMRecoveryPending(ValueError):pass

class LivePointer:
    """One bounded gesture owns the desktop until release or disconnect timeout."""
    idle_timeout=3
    def __init__(self,vm,action):
        self.vm=vm;self.token=uuid.uuid4().hex;self.commands=queue.Queue();self.ready=threading.Event();self.error=None;self.closed=False
        threading.Thread(target=self.run,args=(action,),daemon=True).start()
        if not self.ready.wait(10):raise BusyVM('마우스 연결 시간이 초과되었습니다.')
        if self.error:raise self.error
    def move(self,point):
        self.vm.qmp('input-send-event',{'events':[{'type':'abs','data':{'axis':axis,'value':point[axis]}} for axis in ('x','y')]})
    def run(self,action):
        try:
            with self.vm.exclusive():
                release=[{'type':'btn','data':{'down':False,'button':action['button']}}]
                try:
                    self.move(action);time.sleep(.05)
                    self.vm.qmp('input-send-event',{'events':[{'type':'btn','data':{'down':True,'button':action['button']}}]})
                    time.sleep(.08)
                    self.ready.set();deadline=time.monotonic()+120
                    while time.monotonic()<deadline:
                        try:command=self.commands.get(timeout=self.idle_timeout)
                        except queue.Empty:break
                        done,result,kind,point=command
                        try:
                            self.move(point)
                            if kind=='end':self.vm.qmp('input-send-event',{'events':release})
                            path=self.vm.capture(self.vm.folder/'pointer-screen.png');result['image']=path.read_bytes()
                        except Exception as exc:result['error']=exc
                        finally:done.set()
                        if kind=='end' or 'error' in result:break
                finally:
                    self.vm.qmp('input-send-event',{'events':release})
        except Exception as exc:self.error=exc
        finally:self.closed=True;self.ready.set()
    def request(self,kind,point):
        if self.closed:raise ValueError('마우스 연결이 종료되었습니다. 다시 눌러 주세요.')
        done=threading.Event();result={};self.commands.put((done,result,kind,point))
        if not done.wait(10):raise ValueError('마우스 입력 시간이 초과되었습니다.')
        if 'error' in result:raise result['error']
        return result['image']

def pid_alive(pid):
    if not isinstance(pid,int) or pid<=0:return False
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return ctypes.get_last_error()==5
        try:
            code=ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True
class VM:
    def __init__(self,store):
        self.store=store;self.folder=store.root/'vm'
        if self.folder.resolve()!=store.root.resolve()/'vm':raise ValueError('VM 실행 기록은 데이터 폴더 내부에 보관해야 합니다.')
        self.folder.mkdir(exist_ok=True)
        from vm_readiness import GuestReadiness
        self.readiness=GuestReadiness(self)
        self.pointer_guard=threading.Lock();self.live_pointer=None
        self.qmp_lock=threading.RLock();self.screen_lock=threading.Lock();self.screen_cache=None
        self.thread_lock=threading.Lock();self.session_file=self.folder/'session.json'
        self.process=None;self.starting_file=self.folder/'starting.json'

    def _record(self,path):
        try:original=path.lstat()
        except FileNotFoundError:return None
        except OSError as error:raise ValueError('VM 실행 기록에 접근하지 못했습니다.') from error
        try:
            if not stat.S_ISREG(original.st_mode) or original.st_nlink!=1 or getattr(original,'st_file_attributes',0)&0x400 or original.st_size>65536:raise ValueError('VM 실행 기록 파일 형식을 확인하세요.')
            with path.open('rb') as stream:
                opened=os.fstat(stream.fileno())
                if (opened.st_dev,opened.st_ino)!=(original.st_dev,original.st_ino) or opened.st_nlink!=1:raise ValueError('VM 실행 기록이 읽기 중 변경되었습니다.')
                raw=stream.read(65537)
            if len(raw)>65536:raise ValueError('VM 실행 기록이 너무 큽니다.')
            value=json.loads(raw)
            if not isinstance(value,dict) or type(value.get('port')) is not int or not 1<=value['port']<=65535 or not isinstance(value.get('name'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',value['name']):raise ValueError('VM 실행 기록의 소유 정보를 확인하세요.')
            return value
        except (OSError,ValueError,RecursionError) as error:raise ValueError('VM 실행 기록을 확인하지 못했습니다. 기존 VM 종료 여부를 확인하세요.') from error

    def session(self):
        value=self._record(self.session_file)
        if value is None:return {}
        if type(value.get('pid')) is not int or not 0<value['pid']<=0xffffffff:raise ValueError('VM 실행 기록의 PID를 확인하세요.')
        return value

    def _clear_starting(self,claim):
        if self._record(self.starting_file)==claim:self.starting_file.unlink()

    def _publish_session(self,value):
        temporary=self.folder/('session-'+uuid.uuid4().hex+'.tmp');created=False
        try:
            with temporary.open('xb') as stream:
                created=True;stream.write(json.dumps(value).encode());stream.flush();os.fsync(stream.fileno())
            temporary.replace(self.session_file)
        finally:
            if created:temporary.unlink(missing_ok=True)

    def qmp(self,command,arguments=None):
        with self.qmp_lock:return self._qmp(command,arguments)

    def _qmp(self,command,arguments=None):
        session=self.session()
        if not session:raise ValueError('공유 VM이 실행 중이 아닙니다.')
        deadline=time.monotonic()+5
        with socket.create_connection(('127.0.0.1',session['port']),timeout=3) as sock:
            pending=bytearray()
            def remaining():
                seconds=deadline-time.monotonic()
                if seconds<=0:raise ValueError('VM 제어 응답 대기 시간이 초과되었습니다.')
                sock.settimeout(seconds)
            def read():
                while True:
                    remaining()
                    end=pending.find(b'\n')
                    if end>=0:
                        if end>=1024*1024:raise ValueError('VM 제어 응답이 너무 큽니다.')
                        line=bytes(pending[:end]);del pending[:end+1]
                        try:value=json.loads(line)
                        except (ValueError,RecursionError) as error:raise ValueError('VM 제어 응답 형식 오류') from error
                        if not isinstance(value,dict):raise ValueError('VM 제어 응답은 객체여야 합니다.')
                        return value
                    if len(pending)>=1024*1024:raise ValueError('VM 제어 응답이 너무 큽니다.')
                    chunk=sock.recv(min(65536,1024*1024-len(pending)))
                    if not chunk:raise ValueError('VM 제어 연결이 종료되었습니다.')
                    pending.extend(chunk)
            def send(name,args=None):
                remaining();sock.sendall((json.dumps({'execute':name,'arguments':args or {}})+'\n').encode())
                while True:
                    response=read()
                    if 'error' in response:
                        error=response['error']
                        description=error.get('desc') if isinstance(error,dict) else None
                        raise ValueError('VM 응답 오류: '+(description if isinstance(description,str) else '응답 형식 오류'))
                    if 'return' in response:return response['return']
            if 'QMP' not in read():raise ValueError('QMP 응답이 아닙니다.')
            send('qmp_capabilities')
            identity=send('query-name')
            if not isinstance(identity,dict) or identity.get('name')!=session['name']:raise ValueError('이 프로젝트의 VM이 아닙니다.')
            result=send(command,arguments)
            if command=='query-status' and (not isinstance(result,dict) or not isinstance(result.get('status'),str) or not 1<=len(result['status'])<=80):
                raise ValueError('VM 상태 응답 형식 오류')
            return result

    def owner_record(self):
        value=read_setting(self.store,'vm_owner',None)
        if value is None:return None
        if not isinstance(value,dict) or any(not isinstance(value.get(key),str) or not 1<=len(value[key])<=200 for key in ('task_id','agent','role')):raise ValueError('VM 사용권 기록 형식 오류')
        if value['role'] not in ('planner','developer','reviewer','reporter'):raise ValueError('VM 사용권 역할 오류')
        clean={key:value[key] for key in ('task_id','agent','role')}
        if 'since' in value:
            since=value['since']
            if type(since) not in (int,float) or not math.isfinite(since) or since<0:raise ValueError('VM 사용권 시각 오류')
            clean['since']=since
        return clean

    def status(self):
        config=settings(self.store);state='stopped';error='';session=None
        try:
            session=self.session();pending=self._record(self.starting_file)
            if pending:raise ValueError('VM 시작 확인이 끝나지 않았습니다. 기존 VM 종료 여부와 실행 기록을 확인하세요.')
            if session:
                if not self.qmp_lock.acquire(timeout=.15):
                    state='checking';error='VM 상태 확인 중 · 화면 연결 응답을 기다리고 있습니다.'
                else:
                    try:
                        try:state=self.qmp('query-status')['status']
                        except OSError:
                            if pid_alive(session['pid']):raise
                            state='stopped'
                    finally:self.qmp_lock.release()
        except (OSError,ValueError) as e:error=str(e);state='unreachable'
        owner_unavailable=False
        try:lease=self.owner_record()
        except (ValueError,TypeError,OverflowError,RecursionError):
            lease=None;owner_unavailable=True;state='unreachable';error='VM 사용권 기록을 읽지 못했습니다. 원본을 보존했습니다. 데이터 백업과 실행 상태를 확인하세요.'
        if lease and not self.thread_lock.locked():
            # Test the OS lock too: another worker process may own the desktop.
            try:
                with self.exclusive():lease=None
            except BusyVM:pass
            except (OSError,ValueError) as exc:state='unreachable';error=str(exc)
        readiness=self.readiness.snapshot(state,session or {})
        from boot_timing import observe
        boot_timing=observe(self.store,session or {},state,readiness,configured=config)
        activity=read_setting(self.store,'vm_activity',None)
        if not (activity and lease and activity.get('session')==(session or {}).get('name') and activity.get('task_id')==lease.get('task_id')):activity=None
        return {'activity':activity,'boot_timing':boot_timing,'readiness':readiness,'boot_memory':boot_memory(config),'state':state,'owner':lease,'owner_unavailable':owner_unavailable,'error':error,'installed':(ROOT/'runtime/qemu/qemu-system-x86_64.exe').is_file(),
                'iso_exists':(ROOT/config['iso']).is_file(),'config':config,'shared':True,
                'installation_only':bool(session and session.get('config',{}).get('installation_only')),
                'runtime_memory_mb':session.get('config',{}).get('memory_mb') if session else None,
                'guest_os':session.get('config',{}).get('guest_os','linux') if session else None,'media':read_setting(self.store,'windows_media',{}) if config.get('guest_os')=='windows' else {},'scope':'작업 명령은 선택한 VM 내부에서 실행합니다. 모델 추론과 결과 기록은 호스트에서 수행합니다.'}

    def pointer_input(self,data):
        kind=data.get('action')
        if kind not in ('begin','move','end'):raise ValueError('마우스 동작 오류')
        point=validate_actions([{**data,'action':'click','role':'developer'}],['developer'])[0]
        if kind=='begin':
            with self.pointer_guard:
                if self.live_pointer and not self.live_pointer.closed:raise BusyVM('다른 화면에서 마우스를 사용 중입니다.')
                self.live_pointer=LivePointer(self,point)
                return {'token':self.live_pointer.token}
        session=self.live_pointer
        if not session or data.get('token')!=session.token:raise ValueError('마우스 연결을 확인하세요.')
        return {'image':session.request(kind,point)}

    def desktop_handle(self):
        path=self.folder/'desktop.lock'
        if self.folder.resolve()!=self.folder:raise ValueError('VM 사용권 폴더 경로를 확인하세요.')
        def valid(info):return stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not getattr(info,'st_file_attributes',0)&0x400
        try:original=path.lstat()
        except FileNotFoundError:original=None
        if original is not None and not valid(original):raise ValueError('VM 사용권 잠금은 링크가 아닌 일반 파일이어야 합니다.')
        fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            opened=os.fstat(fd);current=path.lstat()
            identity=lambda info:(info.st_dev,info.st_ino)
            if not valid(opened) or not valid(current) or identity(opened)!=identity(current) or original is not None and identity(original)!=identity(opened) or path.resolve().parent!=self.folder:
                raise ValueError('VM 사용권 잠금 파일이 열기 중 변경되었습니다.')
            return os.fdopen(fd,'r+b',buffering=0)
        except BaseException:
            os.close(fd);raise

    @contextmanager
    def exclusive(self):
        if not self.thread_lock.acquire(blocking=False):raise BusyVM('다른 에이전트가 공유 VM을 사용 중입니다.')
        handle=None;locked=False
        try:
            handle=self.desktop_handle();handle.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                locked=True
            except OSError:raise BusyVM('다른 실행기가 공유 VM을 사용 중입니다.') from None
            if os.fstat(handle.fileno()).st_size==0:
                handle.write(b'0');handle.flush();handle.seek(0)
            yield
        finally:
            try:
                if handle:
                    try:
                        if locked and os.name=='nt':
                            import msvcrt
                            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                    finally:handle.close()
            finally:self.thread_lock.release()

    def save(self,value):
        config={**DEFAULT,**value}
        if config['guest_os'] not in ('windows','linux'):raise ValueError('VM 운영체제는 Windows 또는 Linux를 선택하세요.')
        if any(not isinstance(config[k],bool) for k in ('enabled','network')):raise ValueError('VM 사용과 네트워크 설정을 확인하세요.')
        if type(config['memory_mb']) is not int or not 256<=config['memory_mb']<=32768 or type(config['cpus']) is not int or not 1<=config['cpus']<=8:raise ValueError('VM은 RAM 256~32768MB, CPU 1~8개로 설정하세요.')
        if config['guest_os']=='windows' and (not config['disk'] or config['memory_mb']<2048):raise ValueError('Windows 10 VM에는 영구 디스크와 RAM 2048MB 이상이 필요합니다. 원활한 작업에는 4096MB 이상을 권장합니다.')
        if config['accelerator'] not in ('tcg','whpx'):raise ValueError('가속 방식 오류')
        for key in ('iso','disk'):
            path=config[key]
            if not isinstance(path,str) or (key=='iso' and not path and not config['disk']):raise ValueError('부팅 ISO 경로를 입력하세요.')
            if path:
                if ',' in path:raise ValueError('VM 파일 경로에는 쉼표를 사용할 수 없습니다.')
                resolved=(ROOT/path).resolve()
                if not resolved.is_relative_to(ROOT) or not resolved.is_file():raise ValueError('VM 파일은 프로젝트 폴더 안의 실제 파일이어야 합니다.')
                if resolved.suffix.lower() not in (('.iso',) if key=='iso' else ('.qcow2','.img')):raise ValueError('ISO 또는 qcow2/img 파일을 지정하세요.')
                config[key]=resolved.relative_to(ROOT).as_posix()
        with self.exclusive():
            if self._record(self.starting_file):raise ValueError('VM 시작 확인이 끝나지 않아 설정 변경을 보류합니다.')
            if self.session():
                try:self.qmp('query-status')
                except (OSError,ValueError):
                    if pid_alive(self.session().get('pid')):
                        raise ValueError('기존 VM 프로세스가 실행 중입니다. 제어 연결과 종료 상태를 확인한 뒤 설정을 저장하세요.') from None
                else:raise ValueError('VM을 종료한 뒤 환경 설정을 저장하세요.')
            checked_write_setting(self.store,'vm',{k:config[k] for k in DEFAULT},value)
        return settings(self.store)

    def _start(self,installation_only=False):
        config=settings(self.store)
        if installation_only:
            if config.get('guest_os')!='windows' or not config.get('iso'):
                raise ValueError('Windows 설치 ISO가 지정된 환경에서만 설치 모드를 사용할 수 있습니다.')
            config={**config,'memory_mb':2048,'installation_only':True}
        if not config['enabled']:raise ValueError('설정에서 공유 VM 사용을 켜 주세요.')
        if self._record(self.starting_file):raise ValueError('VM 시작 확인이 끝나지 않았습니다. 중복 VM은 시작하지 않습니다.')
        session=self.session()
        if session:
            try:status=self.qmp('query-status')
            except (OSError,ValueError):
                if pid_alive(session['pid']):raise ValueError('기존 VM 프로세스의 제어 연결을 확인하지 못했습니다. 중복 VM은 시작하지 않습니다.') from None
            else:
                if not isinstance(session.get('config'),dict) or not same_configuration(session['config'],config):raise ValueError('기존 VM의 설정 기록이 현재 설정과 일치하지 않습니다. 중복 VM은 시작하지 않습니다.')
                return status
        if config['accelerator']=='whpx':
            capability=whpx_capability()
            if capability['available'] is not True:raise ValueError(capability['reason']+' TCG를 사용하거나 Windows Hypervisor Platform 설정을 확인한 뒤 다시 시도하세요.')
        executable=ROOT/'runtime/qemu/qemu-system-x86_64.exe';iso=(ROOT/config['iso']).resolve()
        if not executable.is_file() or (config['iso'] and not iso.is_file()) or (not config['iso'] and not config['disk']):raise ValueError('VM 런타임과 부팅 ISO를 먼저 준비하세요. install_vm.py를 실행하세요.')
        admission=boot_memory(config)
        if admission['can_start'] is False:raise ValueError(admission['reason'])
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        name='moa-'+uuid.uuid4().hex
        serial=self.folder/('serial-'+uuid.uuid4().hex+'.log')
        args=[str(executable),'-name',name,'-m',str(config['memory_mb']),'-smp',str(config['cpus']),'-accel',config['accelerator'],
              '-display','none','-vga','virtio' if not config['disk'] else 'std','-qmp',f'tcp:127.0.0.1:{port},server=on,wait=off','-boot','order=dc' if config.get('guest_os')=='windows' and config['iso'] else 'c' if config['disk'] else 'd','-device','usb-tablet','-usb',
              '-nic','user' if config['network'] else 'none','-serial','file:'+str(serial.resolve())]
        if config.get('guest_os')!='windows':args+=['-no-reboot']
        if config['iso']:args+=['-cdrom',str(iso)]
        agent_port=None
        if config.get('guest_os')=='windows':
            with socket.socket() as agent_sock:agent_sock.bind(('127.0.0.1',0));agent_port=agent_sock.getsockname()[1]
            args+=['-device','virtio-serial','-chardev',f'socket,id=moaga,host=127.0.0.1,port={agent_port},server=on,wait=off','-device','virtserialport,chardev=moaga,name=org.qemu.guest_agent.0']
        if config['disk']:args+=['-drive',f"file={ROOT/config['disk']},format={'qcow2' if config['disk'].endswith('.qcow2') else 'raw'},if=ide"]
        claim={'name':name,'port':port,'started':time.time()}
        with self.starting_file.open('x',encoding='utf-8') as stream:
            json.dump(claim,stream);stream.flush();os.fsync(stream.fileno())
        child=None
        try:
            with (self.folder/'qemu.log').open('ab') as output:
                child=subprocess.Popen(args,stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));self.process=child
            self._publish_session({'pid':child.pid,**claim,'config':config,'serial':serial.name,'agent_port':agent_port})
            self._clear_starting(claim)
        except BaseException as error:
            closed=child is None and isinstance(error,OSError)
            if child is not None:
                try:
                    if child.poll() is None:
                        child.terminate()
                        try:child.wait(timeout=5)
                        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                    closed=True;self.process=None
                except (OSError,subprocess.TimeoutExpired):pass
            if closed:
                try:self._clear_starting(claim)
                except (OSError,ValueError):pass
            raise
        for _ in range(50):
            try:return self.qmp('query-status')
            except (OSError,ValueError):
                if self.process.poll() is not None:raise ValueError('VM 시작 실패: data/vm/qemu.log를 확인하세요.')
                time.sleep(.1)
        raise ValueError('VM 제어 연결이 준비되지 않았습니다.')

    def start(self):
        with self.exclusive():return self._start()
    def start_installation(self):
        """Boot Windows setup at its 2GB minimum; never admit normal work here."""
        with self.exclusive():
            result=self._start(installation_only=True)
            if result.get('status')=='paused':self.qmp('cont');return self.qmp('query-status')
            return result
    def restart(self):
        with self.exclusive():
            if not self.session():raise ValueError('실행 중인 VM만 재시작할 수 있습니다. 부팅 버튼을 사용하세요.')
            self._shutdown_gracefully(timeout=90)
            return self._start()

    def shutdown_gracefully(self,timeout=90):
        """Ask the guest OS to shut down; never quit/kill QEMU on timeout."""
        with self.exclusive():return self._shutdown_gracefully(timeout)

    def _shutdown_gracefully(self,timeout=90):
        session=self.session()
        if not session:return {'state':'stopped','requested':False}
        if pid_alive(session['pid']):
            status=self.qmp('query-status')['status']
            if status=='paused':self.qmp('cont')
            method='guest_acpi_shutdown'
            if session.get('config',{}).get('guest_os')=='windows':
                try:
                    import windows_guest
                    info=windows_guest.rpc(self,'guest-get-osinfo',timeout=2)
                    if info.get('id')!='mswindows':raise ValueError('Windows 확인 실패')
                    windows_guest.rpc(self,'guest-exec',{'path':r'C:\Windows\System32\shutdown.exe','arg':['/s','/t','0'],'capture-output':False},timeout=2)
                    method='guest_windows_shutdown'
                except (OSError,ValueError,KeyError):self.qmp('system_powerdown')
            else:self.qmp('system_powerdown')
            deadline=time.monotonic()+timeout
            while pid_alive(session['pid']):
                if time.monotonic()>=deadline:raise ValueError('게스트 정상 종료가 아직 완료되지 않았습니다. VM을 강제 종료하지 않고 실행 기록을 보존했습니다.')
                time.sleep(.25)
        if self.session()!=session:raise ValueError('VM 실행 기록이 변경되어 정리를 보류합니다.')
        self.session_file.unlink(missing_ok=True)
        claim=self._record(self.starting_file)
        if claim and claim['name']==session['name'] and claim['port']==session['port']:self._clear_starting(claim)
        return {'state':'stopped','requested':True,'method':method if 'method' in locals() else 'already_stopped'}

    def stop(self):
        with self.exclusive():return self._stop()

    def _stop(self):
        session=self.session()
        if not session:raise ValueError('공유 VM이 실행 중이 아닙니다.')
        try:self.qmp('quit')
        except (OSError,ValueError):
            if pid_alive(session['pid']):raise ValueError('VM 종료를 확인하지 못했습니다. 실행 기록을 보존합니다.') from None
        if self.process is not None and self.process.pid==session['pid']:
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:raise ValueError('VM 종료 대기 시간이 초과되었습니다. 실행 기록을 보존합니다.') from None
        else:
            for _ in range(20):
                if not pid_alive(session['pid']):break
                time.sleep(.1)
            else:raise ValueError('VM 프로세스 종료를 확인하지 못했습니다. 실행 기록을 보존합니다.')
        if self.session()!=session:raise ValueError('VM 실행 기록이 변경되어 자동 정리를 보류합니다.')
        self.session_file.unlink()
        claim=self._record(self.starting_file)
        if claim and claim['name']==session['name'] and claim['port']==session['port']:self._clear_starting(claim)
        return {'state':'stopped'}


    def screen_frame(self):
        # Observers never acquire the desktop input/agent lease.
        with self.screen_lock:
            session=self.session();key=(session.get('name'),session.get('pid'));now=time.monotonic()
            if self.screen_cache and self.screen_cache[0]==key and now-self.screen_cache[1]<1/60:return self.screen_cache[2]
            path=self.capture(self.folder/'observer-screen.png');data=path.read_bytes()
            self.screen_cache=(key,time.monotonic(),data)
            return data

    def capture(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.qmp('screendump',{'filename':str(path.resolve()),'format':'png'})
        if not path.is_file():raise ValueError('VM 화면 파일이 생성되지 않았습니다.')
        return path

    def guest_history(self,tid):
        with self.store.connect() as c:
            rows=c.execute("SELECT content FROM messages WHERE task_id=? AND kind='guest_result' ORDER BY seq DESC LIMIT 8",(tid,)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def guest_active(self,tid):
        with self.store.connect() as c:
            task=c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()
            paused=c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='true'
        if not task or task[0] not in ('running','queued') or paused:raise ValueError('VM 작업이 중단·일시 중지되었습니다. 자동 재실행하지 않습니다.')

    @contextmanager
    def activity(self,mode,label,tid=None,role=None):
        previous=read_setting(self.store,'vm_activity',None);token=uuid.uuid4().hex
        value={'token':token,'mode':mode,'label':str(label)[:180],'task_id':tid,'role':role,'session':self.session().get('name'),'started_at':time.time()}
        write_setting(self.store,'vm_activity',value)
        try:yield value
        finally:
            current=read_setting(self.store,'vm_activity',None)
            if current and current.get('token')==token:write_setting(self.store,'vm_activity',previous)

    def command(self,tid,role,command,timeout=35,output_limit=16000,terminal_output=True):
        with self.activity('cli','Windows PowerShell 명령 실행 중' if self.session().get('config',{}).get('guest_os')=='windows' else '게스트 터미널 명령 실행 중',tid,role):
            return self._command(tid,role,command,timeout,output_limit,terminal_output)

    def _command(self,tid,role,command,timeout=35,output_limit=16000,terminal_output=True):
        """Type into the guest terminal and read stdout/exit code from its serial port.

        Called while stage() owns the desktop. No command is run by a host shell.
        A timed-out command is never replayed automatically.
        """
        if not self.thread_lock.locked():raise ValueError('VM 사용권 없이 명령을 실행할 수 없습니다.')
        self.guest_active(tid)
        if self.session().get('config',{}).get('guest_os')=='windows':
            import windows_guest
            return windows_guest.command(self,tid,role,command,timeout=max(timeout,120),output_limit=output_limit)
        if not re.fullmatch('[a-f0-9]{12}',tid) or role not in ('planner','developer','reviewer','reporter'):raise ValueError('VM 작업·역할 오류')
        if not isinstance(command,str) or not 1<=len(command)<=1000 or any(ord(c)<32 or ord(c)>126 for c in command):raise ValueError('VM 명령은 ASCII 한 줄 1~1000자입니다.')
        session=self.session();name=session.get('serial','')
        if not re.fullmatch(r'serial-[a-f0-9]{32}\.log',name):raise ValueError('VM 명령 채널을 적용하려면 VM을 종료 후 다시 시작하세요.')
        serial=self.folder/name
        info=serial.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or serial.resolve().parent!=self.folder.resolve() or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('VM 명령 채널 경로 오류')
        token=uuid.uuid4().hex;begin='MOA_BEGIN_'+token;end='MOA_END_'+token
        workspace='/home/tc/moa/'+tid
        script=f"mkdir -p {shlex.quote(workspace)}; cd {shlex.quote(workspace)} || exit 99; printf '{begin}\\n'; timeout 30 sh -c {shlex.quote(command)}; rc=$?; printf '\\n{end}:%s\\n' \"$rc\""
        redirect='2>&1 | tee /dev/ttyS0' if terminal_output else '> /dev/ttyS0 2>&1'
        line='sudo sh -c '+shlex.quote('('+script+') '+redirect)+'\n'
        if len(line)>1000:raise ValueError('VM 터미널 입력 한도를 초과합니다. 명령을 짧게 나누어 실행하세요.')
        # The bundled Tiny Core desktop has a terminal launcher at this location.
        # Preserve other desktops: this bridge is offered only for the bundled ISO.
        if session.get('config',{}).get('iso')!='runtime/vm-downloads/moa-desktop.iso':raise ValueError('현재 게스트 명령 연결은 번들 Tiny Core 데스크톱을 지원합니다.')
        if not getattr(self,'guest_terminal_session',None)==session['name']:
            self.prepare_guest_terminal(session,serial,tid)
        self.action({'action':'key','keys':['ctrl','c']})
        offset=serial.stat().st_size
        for start in range(0,len(line),160):
            self.guest_active(tid)
            self.action({'action':'text','text':line[start:start+160]})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            with self.store.connect() as c:
                paused=c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='true'
                state=c.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()
            if paused:raise ValueError('VM 명령 중 일시 중지됨. 재개 전 게스트 상태를 확인하세요.')
            if not state or state[0] not in ('running','queued'):raise ValueError('중단된 작업의 VM 명령입니다. 자동 재실행하지 않습니다.')
            with serial.open('rb') as stream:stream.seek(offset);data=stream.read(1024*1024)
            text=data.decode('utf-8',errors='replace')
            match=re.search(re.escape(end)+r':(\d+)',text)
            if match and begin in text:
                output=text.split(begin,1)[1].split(end,1)[0].strip()
                return {'command':command,'exit_code':int(match.group(1)),'output':output[:output_limit],
                        'output_truncated':len(output)>output_limit,'runner':'qemu-guest','workspace':workspace}
            if len(data)>=1024*1024:raise ValueError('VM 명령 출력 한도를 초과했습니다. 자동 재실행하지 않습니다.')
            time.sleep(.2)
        raise ValueError('VM 명령 응답 시간이 초과되었습니다. 자동 재실행하지 않습니다. 게스트 상태를 확인하세요.')

    def export_workspace(self,tid,role,workspace):
        if self.session().get('config',{}).get('guest_os')=='windows':
            import windows_guest
            return windows_guest.export_workspace(self,tid,role,workspace)
        result=self.command(tid,role,'tar czf - . | od -An -v -tx1',output_limit=900000,terminal_output=False)
        if result['exit_code'] or result['output_truncated']:raise ValueError('VM 결과 파일 내보내기 실패 또는 크기 한도 초과')
        try:
            data=bytes.fromhex(result['output'])
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
                count=total=0
                for member in archive:
                    count+=1;total+=member.size
                    parts=member.name.replace('\\','/').split('/')
                    if count>512 or total>16*1024*1024 or member.size<0 or member.name.startswith('/') or '..' in parts or not (member.isfile() or member.isdir()):raise ValueError('VM 결과 아카이브 경로·유형·크기 오류')
        except (ValueError,tarfile.TarError) as error:raise ValueError('VM 결과 아카이브를 확인하지 못했습니다.') from error
        path=workspace.path('guest-workspace.tar.gz')
        if path.exists() and (path.is_symlink() or path.stat().st_nlink!=1):raise ValueError('VM 결과 아카이브 링크 오류')
        temporary=path.with_name('guest-export-'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as stream:stream.write(data)
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)
        return {'path':'guest-workspace.tar.gz','bytes':len(data),'runner':'qemu-guest'}

    def prepare_guest_terminal(self,session,serial,tid):
        """Boot readiness is proven by a harmless guest echo, not a screenshot guess."""
        import struct
        marker='MOA_READY_'+uuid.uuid4().hex
        # A previous cancelled input may have left the shell inside a quoted
        # command. Reset only a guest whose serial history proves prior setup;
        # never send Ctrl+C to the first boot's startup services.
        with serial.open('rb') as stream:
            stream.seek(max(0,serial.stat().st_size-65536));prior=stream.read(65536)
        if b'MOA_READY_' in prior or b'MOA_BEGIN_' in prior:
            self.action({'action':'key','keys':['ctrl','c']})
        deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            self.guest_active(tid)
            path=self.capture(self.folder/'terminal-ready.png')
            width,height=struct.unpack('>II',path.read_bytes()[16:24])
            # The bundled Xorg changes from the boot console to 1280x800.
            if width<1000:
                time.sleep(1);continue
            self.action({'action':'click','x':round((width/2+98)/width*32767),'y':round((height-24)/height*32767),'button':'left'})
            time.sleep(.5)
            # Do not interrupt guest startup services while waiting for a shell.
            offset=serial.stat().st_size
            self.action({'action':'text','text':"sudo sh -c 'echo "+marker+" > /dev/ttyS0'\n"})
            for _ in range(30):
                self.guest_active(tid)
                with serial.open('rb') as stream:stream.seek(offset);data=stream.read(65536)
                if marker.encode() in data:
                    self.guest_terminal_session=session['name'];return
                time.sleep(.2)
        raise ValueError('VM 데스크톱·게스트 출력 채널이 준비되지 않았습니다. 부팅 화면을 확인하세요.')

    @contextmanager
    def release_input_on_error(self,events):
        try:yield
        except Exception:
            # A lost reply leaves guest input state unknown. Never replay the
            # press; attempt only release and preserve the original failure.
            try:self.qmp('input-send-event',{'events':events})
            except Exception:pass
            raise

    def action(self,action):
        kind=action['action']
        if kind=='key':
            keys=action['keys']
            releases=[{'type':'key','data':{'down':False,'key':{'type':'qcode','data':key}}} for key in reversed(keys)]
            with self.release_input_on_error(releases):
                for down,sequence in ((True,keys),(False,list(reversed(keys)))):
                    self.qmp('input-send-event',{'events':[{'type':'key','data':{'down':down,'key':{'type':'qcode','data':key}}} for key in sequence]})
                    time.sleep(.15)
        elif kind=='text':
            punctuation={' ':'spc','\n':'ret','\t':'tab','-':'minus','=':'equal','.':'dot',',':'comma','/':'slash','\\':'backslash',';':'semicolon',"'":'apostrophe','`':'grave','[':'bracket_left',']':'bracket_right'}
            shifted=dict(zip('!@#$%^&*()_+<>?:"~{}|','1234567890-=,./;\'`[]\\'))
            for char in action['text']:
                shift=char.isupper() or char in shifted;plain=shifted.get(char,char.lower());key=punctuation.get(plain,plain)
                sequence=([('shift',True)] if shift else [])+[(key,True),(key,False)]+([('shift',False)] if shift else [])
                releases=[{'type':'key','data':{'down':False,'key':{'type':'qcode','data':name}}} for name in ([key,'shift'] if shift else [key])]
                with self.release_input_on_error(releases):
                    self.qmp('input-send-event',{'events':[{'type':'key','data':{'down':down,'key':{'type':'qcode','data':key}}} for key,down in sequence]})
                    time.sleep(.015)
        elif kind=='click':
            events=[{'type':'abs','data':{'axis':axis,'value':action[axis]}} for axis in ('x','y')]
            self.qmp('input-send-event',{'events':events})
            time.sleep(.05)
            with self.release_input_on_error([{'type':'btn','data':{'down':False,'button':action['button']}}]):
                for down in (True,False):
                    self.qmp('input-send-event',{'events':[{'type':'btn','data':{'down':down,'button':action['button']}}]})
                    time.sleep(.1)
        elif kind=='drag':
            def move(point):self.qmp('input-send-event',{'events':[{'type':'abs','data':{'axis':axis,'value':point[axis]}} for axis in ('x','y')]})
            button=action['button'];release=[{'type':'btn','data':{'down':False,'button':button}}]
            move(action)
            with self.release_input_on_error(release):
                self.qmp('input-send-event',{'events':[{'type':'btn','data':{'down':True,'button':button}}]})
                time.sleep(.08)
                for point in action['points']:
                    move(point);time.sleep(.025)
                self.qmp('input-send-event',{'events':release})
        elif kind=='wait':time.sleep(action['seconds'])

    @contextmanager
    def admission(self):
        with ExitStack() as stack:
            try:stack.enter_context(self.exclusive())
            except BusyVM as error:raise VMAdmissionBusy(str(error)) from None
            yield

    @contextmanager
    def stage(self,task,role,actor,before=None):
        plan=json.loads(task['execution_plan']) if task['execution_plan'] else {}
        env=plan.get('environment',{})
        if env.get('kind')!='vm':
            if before:before()
            yield;return
        actions=validate_actions(env.get('actions',[]),plan.get('pipeline',[]))
        with self.admission():
            if not same_configuration(env.get('config') or {},settings(self.store)):raise ValueError('접수 이후 VM 설정이 변경되었습니다. 현재 환경으로 새 작업을 등록하세요.')
            def active():
                with self.store.connect() as c:row=c.execute('SELECT status FROM tasks WHERE id=?',(task['id'],)).fetchone()
                return bool(row and row[0] in ('queued','running'))
            if not active():raise ValueError('취소되거나 종료된 VM 작업입니다.')
            current_session=self.session()
            if current_session.get('config',{}).get('accelerator')=='whpx' and self.qmp('query-status').get('status')=='paused':
                log=self.folder/'qemu.log'
                if log.is_file() and 'WHPX: Unexpected VP exit code 4' in log.read_bytes()[-2048:].decode(errors='replace'):
                    with self.store.connect() as c:
                        count=c.execute("SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='vm_restart_recovery'",(task['id'],)).fetchone()[0]
                    if count>=3:raise ValueError('WHPX 재부팅 오류가 반복되어 자동 복구 3회 후 보류했습니다.')
                    self.capture(self.store.root/'tasks'/task['id']/'vm'/'restart-failure.png')
                    self._stop();self._start()
                    self.store.log(task['id'],'system','vm_restart_recovery','WHPX 재부팅 오류로 정지된 VM을 다시 부팅했습니다. 기존 작업 파일·명령 기록을 확인하여 재개합니다.')
                    raise VMRecoveryPending('WHPX 재부팅 오류 복구 중: Windows 준비 후 중단 단계를 자동 재개합니다.')
            if current_session.get('config',{}).get('guest_os')=='windows' and self.readiness.snapshot('running',current_session).get('state')!='ready':raise VMRecoveryPending('Windows 재부팅·작업 연결 준비 대기: 중단된 단계에서 자동 재개합니다.')
            if before:before()
            if not active():raise ValueError('취소되거나 종료된 VM 작업입니다.')
            self._start()
            session=self.session()
            if session.get('config',{}).get('guest_os')=='windows' and self.readiness.snapshot('running',session).get('state')!='ready':raise VMRecoveryPending('Windows 재부팅·작업 연결 준비 대기: 완료된 단계는 유지하고 중단된 단계에서 자동 재개합니다.')
            self.recovery_replay=task.get('wait_reason')=='vm_recovery'
            self.command_revision=task.get('revision',0)
            owner={'task_id':task['id'],'agent':actor,'role':role,'since':time.time()}
            capture_started=False
            try:
                write_setting(self.store,'vm_owner',owner);self.store.log(task['id'],actor,'vm_acquire',json.dumps(owner,ensure_ascii=False))
                folder=self.store.root/'tasks'/task['id']/'vm'
                stamp=f"r{task['revision']}-{role}"
                base=stamp;visit=2
                while any((folder/(stamp+'-'+position+'.png')).exists() for position in ('before','after')):
                    stamp=f'{base}-visit{visit}';visit+=1
                task['vm_capture_stamp']=stamp
                if not active():raise ValueError('취소되거나 종료된 VM 작업입니다.')
                capture_started=True
                self.capture(folder/(stamp+'-before.png'))
                for action in actions:
                    if action['role']!=role:continue
                    if not active():raise ValueError('취소되거나 종료된 VM 작업입니다.')
                    self.action(action);self.store.log(task['id'],actor,'vm_action',json.dumps(action,ensure_ascii=False))
                if not active():raise ValueError('취소되거나 종료된 VM 작업입니다.')
                yield
                if active():self.capture(folder/(stamp+'-after.png'))
            except Exception:
                try:
                    if capture_started and active():self.capture(folder/(stamp+'-after.png'))
                except (OSError,ValueError):pass
                raise
            finally:
                write_setting(self.store,'vm_owner',None);write_setting(self.store,'vm_activity',None);self.store.log(task['id'],actor,'vm_release','공유 VM 사용 종료')
