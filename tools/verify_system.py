"""HTTP end-to-end verification. No browser/computer-use required."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from socket import socket
import uuid
import hashlib
import os
from contextlib import ExitStack
from app import Store,Worker
from data_backup import backup
from soak import freeze

root = Path(__file__).resolve().parent.parent
with socket() as sock:
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]

def request(path, data=None, header=True):
    if process.poll() is not None:raise RuntimeError('격리 검증 서버가 종료되었습니다.')
    headers = {'Content-Type':'application/json'}
    if header:
        headers['X-Local-Agent']='1'
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,
                               None if data is None else json.dumps(data).encode(), headers)
    with urllib.request.urlopen(req,timeout=3) as r:
        body=r.read()
        return json.loads(body) if 'application/json' in r.headers.get('Content-Type','') else body

with ExitStack() as resources:
    temporary=resources.enter_context(tempfile.TemporaryDirectory(dir=root))
    root=Path(temporary)/'source';freeze(root)
    folder=Path(temporary)/'data';folder.mkdir()
    log=resources.enter_context((Path(temporary)/'server.log').open('wb'))
    args=[sys.executable,str(root/'src/app.py'),'--port',str(port),'--data',folder]
    process=subprocess.Popen(args,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for _ in range(100):
            try:
                request('/api/state');break
            except (urllib.error.URLError,ConnectionError):
                if process.poll() is not None:
                    raise RuntimeError((Path(temporary)/'server.log').read_text(encoding='utf-8',errors='replace')[-4000:])
                time.sleep(.05)
        else:
            raise RuntimeError('서버 시작 실패')
        assert '로컬 에이전트 팀'.encode() in request('/')
        setup=request('/api/setup');assert len(setup['presets'])==3
        observed=request('/api/health');assert observed['app']['pid']==process.pid and observed['worker']['thread_alive'] is True
        assert observed['version']==1 and 'scope' in observed and 'active' in observed['queue']
        diagnosis=request('/api/diagnostics');assert diagnosis['version']==1
        checks={item['id']:item for item in diagnosis['checks']}
        assert len(checks)==len(diagnosis['checks']) and {'python','model_file','runtime_executable','slack_sdk','vm_runtime','application_sources'}<=checks.keys()
        assert checks['application_sources']['state']=='ready' and diagnosis['source_status']['state']=='matching'
        fixture=Store(Path(folder)/'backup-fixture')
        archive=root/'backups'/('test-'+uuid.uuid4().hex+'.zip')
        try:
            backup(fixture.root,archive)
            assert archive.name in request('/api/backups')['files']
            preview=request('/api/backups/inspect',{'name':archive.name})
            assert preview['tasks']==0 and preview['restored'] is False
            for bad in ('../data/state.sqlite3','../outside.zip','missing.zip'):
                try:request('/api/backups/inspect',{'name':bad});raise AssertionError('backup path accepted')
                except urllib.error.HTTPError as e:assert e.code==400
        finally:archive.unlink(missing_ok=True)
        assert len(request('/api/docs'))>=8
        assert 'Slack 연결하기'.encode() in request('/api/docs/slack')
        try:
            request('/api/tasks',{'title':'차단해야 함','mode':'demo'},header=False)
            raise AssertionError('cross-site guard failed')
        except urllib.error.HTTPError as e:
            assert e.code==403
        request('/api/control',{'action':'pause'})
        ids=[request('/api/tasks',{'title':f'HTTP 통합 검증 {i}','mode':'demo'})['id'] for i in range(2)]
        request('/api/messages',{'id':ids[0],'recipient':'reviewer','content':'HTTP addressed instruction'})
        request('/api/control',{'action':'resume'})
        for _ in range(100):
            state=request('/api/state')
            if len(state['tasks'])==2 and all(t['status']=='completed' for t in state['tasks']):break
            time.sleep(.05)
        else:raise AssertionError(state)
        starts=[m['task_id'] for m in state['messages'] if m['kind']=='start']
        assert starts==[ids[0]]*4+[ids[1]]*4, starts
        for tid in ids:
            report=json.loads((Path(folder)/'tasks'/tid/'report.json').read_text(encoding='utf-8'))
            assert report['files'][0]['path']=='demo-result.md'
        artifact_url='/api/artifact?task='+ids[0]+'&file=report.md'
        task_store=Store(folder)
        summary=json.dumps({'summary':'large report fixture '+'x'*1000})
        with task_store.connect() as c:
            c.executemany('INSERT INTO messages(task_id,agent,kind,content,created,recipient,capability) VALUES(?,?,?,?,?,?,?)',[(ids[0],'planner','handoff',summary,time.time(),'developer','planner') for _ in range(2300)])
        Worker(task_store,model='',url='http://127.0.0.1:0').package(ids[0],'completed')
        report_path=Path(folder)/'tasks'/ids[0]/'report.md';payload=report_path.read_bytes()
        assert len(payload)>2*1024**2,'published large-report fixture is too small'
        secret=Path(folder)/'private-test.txt'
        try:
            report_path.write_bytes(payload+b'changed after publication')
            try:request(artifact_url);raise AssertionError('changed artifact served')
            except urllib.error.HTTPError as error:assert error.code==404
            report_path.write_bytes(payload)
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+artifact_url,method='HEAD')
            with urllib.request.urlopen(req,timeout=3) as response:
                assert int(response.headers['Content-Length'])==len(payload) and response.read()==b''
                assert response.headers['X-Content-Type-Options']=='nosniff'
                assert 'moa-'+ids[0] in response.headers['Content-Disposition']
            assert hashlib.sha256(request(artifact_url)).digest()==hashlib.sha256(payload).digest()
            assert any(f['name']=='report.md' for f in request('/api/artifacts?task='+ids[0])['files'])
            secret.write_bytes(payload);report_path.unlink();os.link(secret,report_path)
            try:
                request(artifact_url);raise AssertionError('hardlink artifact served')
            except urllib.error.HTTPError as error:assert error.code==404 and len(error.read())<1000
            assert 'report.md' not in {f['name'] for f in request('/api/artifacts?task='+ids[0])['files']}
        finally:
            report_path.unlink(missing_ok=True);report_path.write_bytes(payload);secret.unlink(missing_ok=True)
        req=urllib.request.Request(f'http://127.0.0.1:{port}/api/vm/screen',method='HEAD')
        try:urllib.request.urlopen(req);raise AssertionError('unsafe HEAD route allowed')
        except urllib.error.HTTPError as error:assert error.code==405
        export=request('/api/logs/export?task='+ids[0]);assert export['count']>0
        assert len(export['messages'])==export['count'] and all(m['task_id']==ids[0] for m in export['messages'])
        filtered=request('/api/logs/export?task='+ids[0]+'&agent=planner')
        from conversation import for_agent
        assert filtered['count']>0 and all(for_agent(m,'planner',export['task']['execution_plan']) for m in filtered['messages'])
        incoming=request('/api/logs?task='+ids[0]+'&agent=reviewer')['messages']
        assert any(m['agent']=='user' and 'HTTP addressed instruction' in m['content'] for m in incoming)
        invalid=urllib.request.Request(f'http://127.0.0.1:{port}/api/tasks',b'\xff',{'Content-Type':'application/json','X-Local-Agent':'1'})
        try:urllib.request.urlopen(invalid);raise AssertionError('invalid UTF-8 accepted')
        except urllib.error.HTTPError as error:assert error.code==400
        assert request('/api/tasks?page='+str(10**40))['tasks']==[]
        second=subprocess.run(args,capture_output=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert second.returncode!=0, 'second worker must be rejected'
        process.terminate();process.wait(timeout=5)
        process=subprocess.Popen(args,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(100):
            try:
                state=request('/api/state');break
            except (urllib.error.URLError,ConnectionError):time.sleep(.05)
        else:raise AssertionError('restart failed')
        assert {task['id'] for task in state['tasks']}==set(ids)
        assert all(t['status']=='completed' for t in state['tasks'])
        history=[message for tid in ids for message in request('/api/logs/export?task='+tid)['messages']]
        assert len([m for m in history if m['kind']=='start'])==8
        scheduled=request('/api/backups/create',{})
        process.wait(timeout=5)
        assert process.returncode==0
        saved=root/'backups'/scheduled['file'];status_file=saved.with_suffix('.status.json')
        try:
            for _ in range(100):
                if status_file.exists() and json.loads(status_file.read_text(encoding='utf-8')).get('state')=='completed':break
                time.sleep(.1)
            else:raise AssertionError('scheduled backup did not complete')
            from data_backup import inspect_backup
            assert inspect_backup(saved)['tasks']==2
            process=subprocess.Popen(args,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for _ in range(100):
                try:
                    recovered=request('/api/backups/request?key='+scheduled['request_key']);break
                except (urllib.error.URLError,ConnectionError):time.sleep(.05)
            else:raise AssertionError('backup request recovery failed')
            assert recovered['state']=='completed'
            replay=request('/api/backups/create',{'request_key':scheduled['request_key']})
            assert replay['state']=='completed' and replay['file']==scheduled['file']
            assert request('/api/diagnostics')['version']==1
            request('/api/control',{'action':'shutdown'});process.wait(timeout=5)
        finally:
            saved.unlink(missing_ok=True);status_file.unlink(missing_ok=True);saved.with_suffix('.job.lock').unlink(missing_ok=True)
        print('PASS: HTTP dashboard, request guard, sequential 2-task execution, real artifacts, single-worker lock, restart persistence, graceful shutdown')
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
