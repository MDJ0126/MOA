"""History boundary and malformed-request checks against a temporary real server."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import json
from pathlib import Path
from socket import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from app import Store
from setup_config import save_team,PRESETS
from tests.test_runtime import node_executable

ROOT=Path(__file__).resolve().parent.parent
with socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
def request(path,data=None):
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,None if data is None else json.dumps(data).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)

with tempfile.TemporaryDirectory(dir=ROOT) as folder:
    store=Store(folder);store.control('pause');save_team(store,PRESETS[0])
    with store.connect() as c:
        for i in range(635):c.execute('INSERT INTO tasks(id,title,mode,status,created) VALUES(?,?,?,?,?)',(f'{i:012x}',f'History {i:03d}','demo','completed',i))
        for i in range(405):c.execute('INSERT INTO messages(task_id,agent,kind,content,created) VALUES(?,?,?,?,?)',('000000000000','developer','handoff',f'log {i}',i))
    process=subprocess.Popen([sys.executable,str(ROOT/'src/app.py'),'--port',str(port),'--data',folder],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:request('/api/state');break
            except OSError:
                if process.poll() is not None:raise RuntimeError(process.communicate())
                time.sleep(.1)
        result=request('/api/tasks?q=History%20000&status=completed&page=0')
        assert result['total']==1 and result['tasks'][0]['id']=='000000000000'
        ids=[];page=0
        while True:
            result=request(f'/api/tasks?status=all&page={page}');ids.extend(t['id'] for t in result['tasks'])
            if not result['has_more']:break
            page+=1
        assert len(ids)==635 and len(set(ids))==635
        logs=[];cursor=None
        while True:
            result=request('/api/logs?task=000000000000'+(f'&before={cursor}' if cursor else ''))
            logs.extend(m['seq'] for m in result['messages']);cursor=result['next_cursor']
            if cursor is None:break
        assert len(logs)==405 and len(set(logs))==405
        for path,data in [('/api/tasks?status=invalid',None),('/api/tasks?page=invalid',None),('/api/tasks',[]),('/api/tasks',{'title':None}),('/api/setup/team',{'agents':[None]})]:
            try:request(path,data);raise AssertionError('invalid input accepted')
            except urllib.error.HTTPError as error:assert error.code==400,(path,error.code)
        node=node_executable()
        subprocess.run([str(node),'tests/test_history.cjs',f'http://127.0.0.1:{port}'],cwd=ROOT,check=True,timeout=25)
        print('PASS: 635 tasks across 22 pages, full-history search, 405 unique logs across 3 pages, malformed requests return 400')
    finally:
        process.terminate();process.wait(timeout=10)
