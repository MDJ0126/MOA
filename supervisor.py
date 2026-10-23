"""Local process supervisor: one model server, one sequential worker/server."""
import json
import os
from pathlib import Path
import signal
import secrets
import subprocess
import sys
import time
import urllib.request

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
    lock=open(path,'a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return lock

def main():
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    data=ROOT/'data';data.mkdir(exist_ok=True)
    lock=acquire(data/'supervisor.lock')
    stop=data/'supervisor.stop'
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
    children={};handles=[];restarts={name:0 for name in commands};next_start={name:0 for name in commands}
    started=time.time()
    def shutdown(*args):stop.touch()
    signal.signal(signal.SIGINT,shutdown);signal.signal(signal.SIGTERM,shutdown)
    print('모델·순차 작업 관리자 감시 시작: http://127.0.0.1:8765',flush=True)
    try:
        while not stop.exists():
            for name,command in commands.items():
                process=children.get(name)
                if process is not None and process.poll() is not None:
                    restarts[name]+=1
                    next_start[name]=time.time()+min(60,2**min(restarts[name],6))
                    children.pop(name)
                    if name=='app' and process.returncode==0:
                        stop.touch();break
                if name not in children and time.time()>=next_start[name]:
                    # Bound growth across restarts; older logs retained once.
                    for suffix in ('stdout','stderr'):
                        path=data/f'{name}.{suffix}.log'
                        if path.exists() and path.stat().st_size>10_000_000:
                            path.replace(path.with_suffix('.previous.log'))
                    stdout=open(data/f'{name}.stdout.log','ab',buffering=0)
                    stderr=open(data/f'{name}.stderr.log','ab',buffering=0)
                    handles.extend([stdout,stderr])
                    children[name]=subprocess.Popen(command,cwd=ROOT,stdout=stdout,stderr=stderr,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            status={'pid':os.getpid(),'started':started,'updated':time.time(),'restarts':restarts,
                    'children':{name:{'pid':p.pid,'running':p.poll() is None} for name,p in children.items()}}
            write_status(data/'supervisor.json',status)
            time.sleep(1)
    finally:
        # Only processes created by this supervisor are stopped.
        app=children.get('app')
        if app is not None and app.poll() is None:
            try:
                req=urllib.request.Request('http://127.0.0.1:8765/api/control',b'{"action":"shutdown"}',
                                           {'Content-Type':'application/json','X-Local-Agent':'1'})
                with urllib.request.urlopen(req,timeout=3):pass
                app.wait(timeout=250)
            except Exception:pass
        for process in children.values():
            if process.poll() is None:process.terminate()
        for process in children.values():
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        for handle in handles:handle.close()
        lock.close()

if __name__=='__main__':main()
