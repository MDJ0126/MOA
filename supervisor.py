"""Local process supervisor: one model server, one sequential worker/server."""
import json
import os
from pathlib import Path
import signal
import secrets
import subprocess
import sys
import socket
import time
from app_lifecycle import request_stop

ROOT=Path(__file__).resolve().parent

def write_status(path,status):
    """A transient Windows reader lock must not terminate managed processes."""
    temp=path.with_suffix(path.suffix+'.tmp')
    for attempt in range(5):
        try:
            temp.write_text(json.dumps(status),encoding='utf-8');temp.replace(path)
            return True
        except OSError:
            if attempt<4:time.sleep(.05)
    return False

def acquire(path):
    lock=open(path,'a+b')
    try:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:
        lock.close()
        raise
    return lock


class ManagedChild:
    """Own exactly one child and its two log handles, including failed launches."""
    def __init__(self,name,command,cwd,logs,clock=time.monotonic,stable_seconds=60):
        self.name=name;self.command=command;self.cwd=cwd;self.logs=Path(logs);self.clock=clock
        self.stable_seconds=stable_seconds;self.process=None;self.handles=[]
        self.restarts=0;self.launch_failures=0;self.consecutive=0;self.next_start=0;self.started=None;self.last_error='';self.last_exit=None

    def release_logs(self):
        for handle in self.handles:handle.close()
        self.handles=[]

    def defer(self,now):
        self.consecutive+=1;self.next_start=now+min(60,2**min(self.consecutive,6))

    def step(self):
        now=self.clock();exited=None
        if self.process is not None:
            if now-self.started>=self.stable_seconds:self.consecutive=0
            exited=self.process.poll()
            if exited is not None:
                self.last_exit=exited;self.release_logs();self.process=None;self.restarts+=1;self.defer(now)
        if self.process is None and now>=self.next_start:
            try:
                self.logs.mkdir(parents=True,exist_ok=True)
                for suffix in ('stdout','stderr'):
                    path=self.logs/f'{self.name}.{suffix}.log'
                    if path.exists() and path.stat().st_size>10_000_000:path.replace(path.with_suffix('.previous.log'))
                    self.handles.append(open(path,'ab',buffering=0))
                self.process=subprocess.Popen(self.command,cwd=self.cwd,stdout=self.handles[0],stderr=self.handles[1],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.started=now;self.last_error=''
            except OSError as error:
                self.release_logs();self.process=None;self.launch_failures+=1;self.defer(now)
                self.last_error=f'시작 실패: {type(error).__name__} ({getattr(error,"winerror",None) or error.errno})'
        return exited

    def status(self):
        return {'pid':self.process.pid if self.process else None,'running':self.process is not None and self.process.poll() is None,
                'restarts':self.restarts,'launch_failures':self.launch_failures,'retry_in_seconds':max(0,self.next_start-self.clock()) if self.process is None else 0,
                'open_log_handles':sum(not h.closed for h in self.handles),'error':self.last_error,'last_exit_code':self.last_exit}

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        finally:self.release_logs()

def check_ports(ports=(8765,8766)):
    for port in ports:
        with socket.socket() as candidate:
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):candidate.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            try:candidate.bind(('127.0.0.1',port))
            except OSError:raise RuntimeError('127.0.0.1:'+str(port)+' 포트를 열 수 없습니다. 기존 앱을 해당 프로젝트에서 종료하거나 다른 포트를 선택하세요. 자동 재사용하지 않습니다.') from None


def main():
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    data=ROOT/'data';data.mkdir(exist_ok=True)
    with acquire(data/'supervisor.lock'):
        stop=data/'supervisor.stop'
        check_ports()
        stop.unlink(missing_ok=True)
        runtime=ROOT/config['runtime_executable'];model=ROOT/config['model_file']
        if not runtime.exists() or not model.exists():raise RuntimeError('로컬 모델을 먼저 install_model.py로 설치하세요.')
        keyfile=data/'model.key'
        if not keyfile.exists():keyfile.write_text(secrets.token_urlsafe(32),encoding='utf-8')
        commands={
            'model':[str(runtime),'-m',str(model),'--host','127.0.0.1','--port','8766','--ctx-size',str(config['context_size']),
                     '--parallel','1','--threads',str(config['threads']),'--gpu-layers','0','--alias',config['model'],
                     '--cache-ram','64','--sleep-idle-seconds','30','--api-key-file',str(keyfile)],
            'app':[sys.executable,'-u',str(ROOT/'app.py'),'--model',config['model'],'--model-url',config['model_url']],
        }
        children={name:ManagedChild(name,command,ROOT,data) for name,command in commands.items()}
        started=time.time()
        def shutdown(*args):stop.touch()
        signal.signal(signal.SIGINT,shutdown);signal.signal(signal.SIGTERM,shutdown)
        print('모델·순차 작업 관리자 감시 시작: http://127.0.0.1:8765',flush=True)
        try:
            while not stop.exists():
                for name,child in children.items():
                    if child.step()==0 and name=='app':stop.touch();break
                status={'pid':os.getpid(),'started':started,'updated':time.time(),'restarts':{n:c.restarts for n,c in children.items()},
                        'children':{name:child.status() for name,child in children.items()}}
                write_status(data/'supervisor.json',status)
                time.sleep(1)
        finally:
            # Only processes created by this supervisor are stopped.
            app=children['app'].process
            if app is not None and app.poll() is None:
                try:
                    request_stop(ROOT,8765,expected_pid=app.pid)
                    app.wait(timeout=250)
                except Exception:pass
            for child in children.values():child.close()

if __name__=='__main__':main()
