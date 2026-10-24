"""Isolated, frozen-source demo queue/HTTP soak. No real model, VM, or Slack."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile
from supervisor import ManagedChild,acquire,write_status
from app_lifecycle import request_stop

ROOT=Path(__file__).resolve().parent.parent
MODULES=('source_state','app_lifecycle','stop_app','private_files','vbox_install','vbox_setup','environment_profiles','app','artifacts','backup_jobs','check_process','checks','conversation','data_backup','diagnostics','health','integrations','local_model','model_transport','reports','setup_config','slack_service','supervisor','vm_runtime')


def freeze(destination):
    destination.mkdir();(destination/'src').mkdir();(destination/'src/web').mkdir()
    for name in MODULES:shutil.copyfile(ROOT/'src'/(name+'.py'),destination/'src'/(name+'.py'))
    for name in ('dashboard.html','ui.css','ui.js'):shutil.copyfile(ROOT/'src/web'/name,destination/'src/web'/name)
    shutil.copytree(ROOT/'docs',destination/'docs')
    config={'default_mode':'demo','model':'soak-demo','model_description':'demo-no-inference','model_url':'http://127.0.0.1:0','engine':{'enabled':False},'slack':{'enabled':False}}
    (destination/'config.json').write_text(json.dumps(config),encoding='utf-8')
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (destination/'src').glob('*.py')}


def verify_bundle(data,tid):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        report=json.loads(archive.read('report.json'))
        if report['task_id']!=tid or report['mode']!='demo' or report['status']!='completed':raise ValueError('unexpected demo report')
        for item in report['files']:
            if hashlib.sha256(archive.read('workspace/'+item['path'].replace('\\','/'))).hexdigest()!=item['sha256']:raise ValueError('artifact hash mismatch')
        return report['report_id']


def stop_owned_app(child,source,port):
    """Use project/instance ownership; never send shutdown to a reused TCP port."""
    try:
        if child.process is not None and child.process.poll() is None:
            try:
                request_stop(source,port,expected_pid=child.process.pid)
                child.process.wait(timeout=10)
            except (OSError,ValueError,TimeoutError, subprocess.TimeoutExpired):pass
    finally:child.close()


def checked_tree(root,ignore_missing=False):
    """Inspect only ordinary entries; never follow a link out of a test run."""
    root=Path(root)
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('unsafe soak directory')
    total=0
    for entry in root.iterdir():
        try:info=entry.lstat()
        except FileNotFoundError:
            if ignore_missing:continue
            raise
        if getattr(info,'st_file_attributes',0)&0x400 or stat.S_ISLNK(info.st_mode):raise ValueError('linked soak entry')
        if stat.S_ISDIR(info.st_mode):total+=checked_tree(entry,ignore_missing)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:total+=info.st_size
        else:raise ValueError('unsafe soak entry')
    return total


def prune_verified(directory,tid):
    """Only a verified task in this newly created, isolated run is eligible."""
    if not isinstance(tid,str) or not re.fullmatch(r'[0-9a-f]{12}',tid):raise ValueError('invalid soak task id')
    try:
        tasks=Path(directory)/'data'/'tasks'
        for parent in (Path(directory),Path(directory)/'data',tasks):
            info=parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('unsafe soak parent')
        target=tasks/tid
        checked_tree(target)
        shutil.rmtree(target)
    except OSError as error:raise RuntimeError('verified soak artifact cleanup failed') from error


def run(directory,seconds=86400,interval=30,job_interval=300,task_timeout=60,keep_artifacts=10,max_bytes=256*1024**2):
    if any(not math.isfinite(value) or value<=0 for value in (seconds,interval,job_interval,task_timeout)):raise ValueError('기간·간격은 유한한 양수여야 합니다.')
    if type(keep_artifacts) is not int or keep_artifacts<0 or type(max_bytes) is not int or max_bytes<=0:raise ValueError('보관 수·용량 제한을 확인하세요.')
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    with acquire(directory/'run.lock'):
        sources=freeze(directory/'source')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        child=ManagedChild('app',[sys.executable,'-u',str(directory/'source/src/app.py'),'--data',str(directory/'data'),'--port',str(port)],directory/'source',directory/'logs')
        started=time.monotonic();deadline=started+seconds;next_sample=started;next_job=started;pending=None;pending_since=None;request_key=None;last_sample=None;sequence=0;last_write=0;next_size_check=started
        result={'version':1,'run_id':uuid.uuid4().hex,'pid':os.getpid(),'status':'running','requested_seconds':seconds,'started_at':time.time(),
                'scope':'동결 소스의 데모 큐·HTTP·결과물 관측. 실제 모델·VM·Slack·게임 엔진은 검증하지 않습니다.',
                'actual_model_tested':False,'demo_24h_observed':False,'samples':0,'verified_tasks':0,'connection_failures':0,'max_sample_gap':0,'sources':sources,
                'task_timeout':task_timeout,'keep_artifacts':keep_artifacts,'max_bytes':max_bytes,'retained_artifacts':[],'pruned_tasks':0}
        def save():
            result.update(updated_at=time.time(),elapsed_seconds=time.monotonic()-started,child=child.status(),pending_task=pending)
            if not write_status(directory/'status.json',result):raise OSError('soak status write failed')
        def request(path,payload=None):
            req=urllib.request.Request(base+path,None if payload is None else json.dumps(payload).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
            with urllib.request.urlopen(req,timeout=3) as response:
                data=response.read(2*1024**2+1)
                if len(data)>2*1024**2:raise ValueError('unexpected response size')
                return data if 'application/zip' in response.headers.get('Content-Type','') else json.loads(data)
        save()
        try:
            while time.monotonic()<deadline or pending:
                if (directory/'stop').exists():result['status']='stopped';break
                now=time.monotonic()
                if pending_since is not None and now-pending_since>=task_timeout:raise TimeoutError('demo task exceeded task timeout')
                if now>=next_size_check:
                    result['observed_bytes']=checked_tree(directory,ignore_missing=True);next_size_check=now+5
                    if result['observed_bytes']>max_bytes:raise ValueError('soak directory exceeded byte budget')
                child.step()
                if child.process is None or child.process.poll() is not None:time.sleep(.2);continue
                if not pending and now<next_sample and now<next_job:
                    if now-last_write>=5:save();last_write=now
                    time.sleep(min(1,interval));continue
                try:
                    health=request('/api/health')
                    if health['app']['pid']!=child.process.pid or health['app_source_sha256']!=sources['app.py']:raise ValueError('server identity/source mismatch')
                    if not health['worker']['thread_alive']:raise ValueError('worker thread stopped')
                    if health['queue']['paused']:raise ValueError('isolated demo queue paused')
                    if now>=next_sample:
                        if last_sample is not None:result['max_sample_gap']=max(result['max_sample_gap'],now-last_sample)
                        last_sample=now;result['samples']+=1;next_sample=now+interval
                    if pending:
                        tasks=request('/api/tasks?status=all&page=0')['tasks'];task=next((t for t in tasks if t['id']==pending),None)
                        if task and task['status']=='completed':
                            report_id=verify_bundle(request('/api/artifact?task='+pending+'&file=artifacts.zip'),pending)
                            result['verified_tasks']+=1;result['retained_artifacts'].append({'task_id':pending,'report_id':report_id})
                            while len(result['retained_artifacts'])>keep_artifacts:
                                prune_verified(directory,result['retained_artifacts'][0]['task_id'])
                                result['retained_artifacts'].pop(0);result['pruned_tasks']+=1
                            pending=None;pending_since=None
                        elif task and task['status'] in ('failed','cancelled','interrupted'):raise ValueError('demo task did not complete')
                    if not pending and now<deadline and now>=next_job:
                        if request_key is None:sequence+=1;request_key=result['run_id']+'-'+str(sequence)
                        pending=request('/api/tasks',{'title':'Isolated demo soak '+str(sequence),'mode':'demo','max_revisions':0,'request_key':request_key})['id'];pending_since=time.monotonic();request_key=None;next_job=now+job_interval
                except (OSError,TimeoutError):result['connection_failures']+=1
                if now-last_write>=1:save();last_write=now
                time.sleep(.2 if pending else min(1,interval))
            else:result['status']='completed'
            elapsed=time.monotonic()-started
            if result['status']=='completed' and not result['verified_tasks']:raise ValueError('no completed demo tasks verified')
            result['demo_24h_observed']=result['status']=='completed' and seconds>=86400 and elapsed>=86400 and result['samples']>=int(seconds/interval)-2 and result['max_sample_gap']<=max(5,interval*2)
        except KeyboardInterrupt:result['status']='stopped'
        except Exception as error:result.update(status='failed',error=type(error).__name__+': '+str(error))
        finally:
            try:stop_owned_app(child,directory/'source',port)
            except Exception as error:
                detail=type(error).__name__+': '+str(error)
                result.update(status='failed',demo_24h_observed=False,cleanup_error=detail)
                result.setdefault('error',detail)
            finally:save()
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours',type=float,default=24);parser.add_argument('--interval',type=float,default=30);parser.add_argument('--job-interval',type=float,default=300)
    parser.add_argument('--directory',type=Path,default=None)
    parser.add_argument('--task-timeout',type=float,default=60);parser.add_argument('--keep-artifacts',type=int,default=10)
    parser.add_argument('--max-mib',type=int,default=256)
    args=parser.parse_args();destination=args.directory or ROOT/'runtime/soak'/uuid.uuid4().hex
    output=run(destination,args.hours*3600,args.interval,args.job_interval,args.task_timeout,args.keep_artifacts,args.max_mib*1024**2)
    print(json.dumps({'directory':str(destination),'status':output['status'],'demo_24h_observed':output['demo_24h_observed']},ensure_ascii=False))
    raise SystemExit(0 if output['status']=='completed' else 1)
