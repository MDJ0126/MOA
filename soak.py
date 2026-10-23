"""Isolated, frozen-source demo queue/HTTP soak. No real model, VM, or Slack."""
import argparse
import hashlib
import io
import json
import math
import os
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

ROOT=Path(__file__).resolve().parent
MODULES=('app_lifecycle','stop_app','private_files','vbox_install','vbox_setup','environment_profiles','app','artifacts','backup_jobs','check_process','checks','conversation','data_backup','diagnostics','health','integrations','local_model','model_transport','reports','setup_config','slack_service','supervisor','vm_runtime')


def freeze(destination):
    destination.mkdir()
    for name in MODULES:shutil.copyfile(ROOT/(name+'.py'),destination/(name+'.py'))
    for name in ('dashboard.html','ui.css','ui.js'):shutil.copyfile(ROOT/name,destination/name)
    shutil.copytree(ROOT/'docs',destination/'docs')
    config={'default_mode':'demo','model':'soak-demo','model_description':'demo-no-inference','model_url':'http://127.0.0.1:0','engine':{'enabled':False},'slack':{'enabled':False}}
    (destination/'config.json').write_text(json.dumps(config),encoding='utf-8')
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.glob('*.py')}


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


def run(directory,seconds=86400,interval=30,job_interval=300):
    if any(not math.isfinite(value) or value<=0 for value in (seconds,interval,job_interval)):raise ValueError('기간·간격은 유한한 양수여야 합니다.')
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    with acquire(directory/'run.lock'):
        sources=freeze(directory/'source')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        child=ManagedChild('app',[sys.executable,'-u',str(directory/'source/app.py'),'--data',str(directory/'data'),'--port',str(port)],directory/'source',directory/'logs')
        started=time.monotonic();deadline=started+seconds;next_sample=started;next_job=started;pending=None;request_key=None;last_sample=None;sequence=0;last_write=0
        result={'version':1,'run_id':uuid.uuid4().hex,'pid':os.getpid(),'status':'running','requested_seconds':seconds,'started_at':time.time(),
                'scope':'동결 소스의 데모 큐·HTTP·결과물 관측. 실제 모델·VM·Slack·게임 엔진은 검증하지 않습니다.',
                'actual_model_tested':False,'demo_24h_observed':False,'samples':0,'verified_tasks':0,'connection_failures':0,'max_sample_gap':0,'sources':sources}
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
                now=time.monotonic();child.step()
                if now-deadline>60:raise TimeoutError('pending demo task did not finish')
                if child.process is None or child.process.poll() is not None:time.sleep(.2);continue
                if not pending and now<next_sample and now<next_job:
                    if now-last_write>=5:save();last_write=now
                    time.sleep(min(1,interval));continue
                try:
                    health=request('/api/health')
                    if health['app']['pid']!=child.process.pid or health['app_source_sha256']!=sources['app.py']:raise ValueError('server identity/source mismatch')
                    if not health['worker']['thread_alive']:raise ValueError('worker thread stopped')
                    if now>=next_sample:
                        if last_sample is not None:result['max_sample_gap']=max(result['max_sample_gap'],now-last_sample)
                        last_sample=now;result['samples']+=1;next_sample=now+interval
                    if pending:
                        tasks=request('/api/tasks?status=all&page=0')['tasks'];task=next((t for t in tasks if t['id']==pending),None)
                        if task and task['status']=='completed':verify_bundle(request('/api/artifact?task='+pending+'&file=artifacts.zip'),pending);result['verified_tasks']+=1;pending=None
                        elif task and task['status'] in ('failed','cancelled','interrupted'):raise ValueError('demo task did not complete')
                    if not pending and now<deadline and now>=next_job:
                        if request_key is None:sequence+=1;request_key=result['run_id']+'-'+str(sequence)
                        pending=request('/api/tasks',{'title':'Isolated demo soak '+str(sequence),'mode':'demo','max_revisions':0,'request_key':request_key})['id'];request_key=None;next_job=now+job_interval
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
            stop_owned_app(child,directory/'source',port);save()
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours',type=float,default=24);parser.add_argument('--interval',type=float,default=30);parser.add_argument('--job-interval',type=float,default=300)
    parser.add_argument('--directory',type=Path,default=None)
    args=parser.parse_args();destination=args.directory or ROOT/'runtime/soak'/uuid.uuid4().hex
    output=run(destination,args.hours*3600,args.interval,args.job_interval)
    print(json.dumps({'directory':str(destination),'status':output['status'],'demo_24h_observed':output['demo_24h_observed']},ensure_ascii=False))
    raise SystemExit(0 if output['status']=='completed' else 1)
