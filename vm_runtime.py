"""One project-owned headless QEMU desktop with exclusive per-agent access."""
from contextlib import contextmanager,ExitStack
import json
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
from setup_config import read_setting,write_setting,checked_write_setting,hardware

ROOT=Path(__file__).resolve().parent
DEFAULT={'enabled':False,'memory_mb':512,'cpus':1,'accelerator':'tcg','iso':'runtime/vm-downloads/moa-desktop.iso','disk':'','network':False}
KEYS=set('abcdefghijklmnopqrstuvwxyz0123456789')|{'ret','esc','spc','tab','backspace','up','down','left','right','home','end','delete','shift','ctrl','alt','meta_l','minus','equal','dot','comma','slash','backslash','semicolon','apostrophe','grave','bracket_left','bracket_right'}|{f'f{i}' for i in range(1,13)}

def settings(store):return read_setting(store,'vm',DEFAULT)

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
        elif action=='click':
            for axis in ('x','y'):
                value=entry.get(axis)
                if not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=32767:raise ValueError('마우스 좌표는 0~32767 사이 정수입니다.')
                clean[axis]=value
            clean['button']=entry.get('button','left')
            if clean['button'] not in ('left','right','middle'):raise ValueError('마우스 버튼 오류')
        elif action=='wait':
            seconds=entry.get('seconds',1)
            if not isinstance(seconds,(int,float)) or not 0<=seconds<=5:raise ValueError('대기는 동작당 0~5초입니다.')
            clean['seconds']=seconds
        elif action!='capture':raise ValueError('지원하지 않는 VM 화면 동작입니다.')
        result.append(clean)
    return result

class BusyVM(ValueError):pass
class VMAdmissionBusy(BusyVM):pass
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
        config=settings(self.store);state='stopped';error=''
        try:
            session=self.session();pending=self._record(self.starting_file)
            if pending:raise ValueError('VM 시작 확인이 끝나지 않았습니다. 기존 VM 종료 여부와 실행 기록을 확인하세요.')
            if session:state=self.qmp('query-status')['status']
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
        return {'state':state,'owner':lease,'owner_unavailable':owner_unavailable,'error':error,'installed':(ROOT/'runtime/qemu/qemu-system-x86_64.exe').is_file(),
                'iso_exists':(ROOT/config['iso']).is_file(),'config':config,'shared':True,
                'scope':'VM 화면 입력·캡처만 격리합니다. 모델 추론과 파일 생성·Python 검증은 호스트에서 실행합니다.'}

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
        if any(not isinstance(config[k],bool) for k in ('enabled','network')):raise ValueError('VM 사용과 네트워크 설정을 확인하세요.')
        if type(config['memory_mb']) is not int or not 256<=config['memory_mb']<=4096 or type(config['cpus']) is not int or not 1<=config['cpus']<=2:raise ValueError('VM은 RAM 256~4096MB, CPU 1~2개로 설정하세요.')
        if config['accelerator'] not in ('tcg','whpx'):raise ValueError('가속 방식 오류')
        for key in ('iso','disk'):
            path=config[key]
            if not isinstance(path,str) or (key=='iso' and not path):raise ValueError('부팅 ISO 경로를 입력하세요.')
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

    def _start(self):
        config=settings(self.store)
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
        if not executable.is_file() or not iso.is_file():raise ValueError('VM 런타임과 부팅 ISO를 먼저 준비하세요. install_vm.py를 실행하세요.')
        available=hardware().get('available_ram_gib')
        if available is not None and available<config['memory_mb']/1024+1:raise ValueError('호스트 여유 RAM이 부족합니다. 모델 유휴 해제를 기다리거나 다른 앱을 정리하세요.')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        name='moa-'+uuid.uuid4().hex
        args=[str(executable),'-name',name,'-m',str(config['memory_mb']),'-smp',str(config['cpus']),'-accel',config['accelerator'],
              '-display','none','-vga','virtio' if not config['disk'] else 'std','-qmp',f'tcp:127.0.0.1:{port},server=on,wait=off','-cdrom',str(iso),'-boot','c' if config['disk'] else 'd','-device','usb-tablet','-usb',
              '-nic','user' if config['network'] else 'none','-no-reboot']
        if config['disk']:args+=['-drive',f"file={ROOT/config['disk']},format={'qcow2' if config['disk'].endswith('.qcow2') else 'raw'},if=ide"]
        claim={'name':name,'port':port,'started':time.time()}
        with self.starting_file.open('x',encoding='utf-8') as stream:
            json.dump(claim,stream);stream.flush();os.fsync(stream.fileno())
        child=None
        try:
            with (self.folder/'qemu.log').open('ab') as output:
                child=subprocess.Popen(args,stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));self.process=child
            self._publish_session({'pid':child.pid,**claim,'config':config})
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
    def stop(self):
        with self.exclusive():
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


    def capture(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.qmp('screendump',{'filename':str(path.resolve()),'format':'png'})
        if not path.is_file():raise ValueError('VM 화면 파일이 생성되지 않았습니다.')
        return path

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
            shifted=dict(zip('!@#$%^&*()_+<>?:"~{}|','1234567890-=.,/;\'`[]\\'))
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
            for down in (True,False):
                self.qmp('input-send-event',{'events':[{'type':'btn','data':{'down':down,'button':action['button']}}]})
                time.sleep(.1)
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
            if before:before()
            if not active():raise ValueError('취소되거나 종료된 VM 작업입니다.')
            self._start();owner={'task_id':task['id'],'agent':actor,'role':role,'since':time.time()}
            capture_started=False
            try:
                write_setting(self.store,'vm_owner',owner);self.store.log(task['id'],actor,'vm_acquire',json.dumps(owner,ensure_ascii=False))
                folder=self.store.root/'tasks'/task['id']/'vm'
                stamp=f"r{task['revision']}-{role}"
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
                write_setting(self.store,'vm_owner',None);self.store.log(task['id'],actor,'vm_release','공유 VM 사용 종료')
