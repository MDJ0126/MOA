"""HTTP end-to-end verification. No browser/computer-use required."""
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from socket import socket

root = Path(__file__).resolve().parent
with socket() as sock:
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]

def request(path, data=None, header=True):
    headers = {'Content-Type':'application/json'}
    if header:
        headers['X-Local-Agent']='1'
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,
                               None if data is None else json.dumps(data).encode(), headers)
    with urllib.request.urlopen(req,timeout=3) as r:
        body=r.read()
        return json.loads(body) if 'application/json' in r.headers.get('Content-Type','') else body

with tempfile.TemporaryDirectory(dir=root) as folder:
    args=[sys.executable,str(root/'app.py'),'--port',str(port),'--data',folder]
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                request('/api/state');break
            except (urllib.error.URLError,ConnectionError):
                if process.poll() is not None:
                    raise RuntimeError(process.communicate())
                time.sleep(.05)
        else:
            raise RuntimeError('서버 시작 실패')
        assert '로컬 에이전트 팀'.encode() in request('/')
        setup=request('/api/setup');assert len(setup['presets'])==3
        assert len(request('/api/docs'))>=8
        assert 'Slack 연결하기'.encode() in request('/api/docs/slack')
        try:
            request('/api/tasks',{'title':'차단해야 함','mode':'demo'},header=False)
            raise AssertionError('cross-site guard failed')
        except urllib.error.HTTPError as e:
            assert e.code==403
        ids=[request('/api/tasks',{'title':f'HTTP 통합 검증 {i}','mode':'demo'})['id'] for i in range(2)]
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
        export=request('/api/logs/export?task='+ids[0]);assert export['count']>0
        assert len(export['messages'])==export['count'] and all(m['task_id']==ids[0] for m in export['messages'])
        filtered=request('/api/logs/export?task='+ids[0]+'&agent=planner');assert filtered['count']>0 and all(m['agent']=='planner' for m in filtered['messages'])
        invalid=urllib.request.Request(f'http://127.0.0.1:{port}/api/tasks',b'\xff',{'Content-Type':'application/json','X-Local-Agent':'1'})
        try:urllib.request.urlopen(invalid);raise AssertionError('invalid UTF-8 accepted')
        except urllib.error.HTTPError as error:assert error.code==400
        assert request('/api/tasks?page='+str(10**40))['tasks']==[]
        second=subprocess.run(args,capture_output=True,timeout=5)
        assert second.returncode!=0, 'second worker must be rejected'
        process.terminate();process.wait(timeout=5)
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                state=request('/api/state');break
            except (urllib.error.URLError,ConnectionError):time.sleep(.05)
        assert all(t['status']=='completed' for t in state['tasks'])
        assert len([m for m in state['messages'] if m['kind']=='start'])==8
        request('/api/control',{'action':'shutdown'})
        process.wait(timeout=5)
        assert process.returncode==0
        print('PASS: HTTP dashboard, request guard, sequential 2-task execution, real artifacts, single-worker lock, restart persistence, graceful shutdown')
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
