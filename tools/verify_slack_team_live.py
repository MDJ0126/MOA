"""Verify real Slack identities, recipient DM receipts, images and guest work.

No credentials are printed. Without --run this only reads local connection status.
"""
import argparse
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import time
import urllib.request
import uuid
import zipfile

ROOT=Path(__file__).resolve().parent.parent


def call(path,data=None):
    req=urllib.request.Request('http://127.0.0.1:8765'+path,None if data is None else json.dumps(data).encode(),
                               {'Content-Type':'application/json','X-Local-Agent':'1'})
    with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');args=parser.parse_args()
    setup=call('/api/setup');state=call('/api/state');config=setup['slack']
    ready=bool(config.get('enabled') and config.get('collaboration') and state.get('slack_status',{}).get('state')=='connected')
    status={'ready':ready,'scope':'Actual Slack channel collaboration, individual bot DMs and actual VM; no fake API clients.',
            'connections':[{'agent_id':c['agent_id'],'has_bot_token':c.get('has_bot_token',False),'has_app_token':c.get('has_app_token',False)} for c in config['connections']]}
    if not ready:
        status['status']='needs_authentication';status['message']='MOA의 팀원 봇 추가에서 최초 Slack 승인을 완료하세요. 봇 접속 정보는 자동으로 저장합니다.'
        (ROOT/'reports/slack-vm-implementation/slack-team-live-status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(status,ensure_ascii=False));return
    if not args.run:print(json.dumps(status,ensure_ascii=False));return
    if state['paused'] or any(t['status'] in ('queued','running') for t in state['tasks']):raise RuntimeError('작업 관리자가 재개 상태이고 다른 작업이 없을 때 검증하세요.')
    title='[Slack 팀원 실제 검증] Create ready.txt containing exactly TEAM_DM_READY followed by newline. Developer: run echo TEAM_DM_READY > ready.txt and cat ready.txt. Reviewer: independently run test "$(cat ready.txt)" = TEAM_DM_READY && echo REVIEW_PASS. Share actual VM screenshots and evidence with your teammate in the team channel. No network, no background commands.'
    started=time.time()
    tid=call('/api/tasks',{'title':title,'mode':'llama','environment':'guest','required_files':['ready.txt'],'max_revisions':1,'auto_recover':False,'request_key':'ui_slack_live_'+uuid.uuid4().hex})['id']
    print('Actual Slack + VM task: '+tid,flush=True)
    deadline=time.time()+900
    while time.time()<deadline:
        task=next(t for t in call('/api/state')['tasks'] if t['id']==tid)
        if task['status'] in ('completed','failed','cancelled','interrupted'):break
        time.sleep(1)
    if task['status']!='completed':raise RuntimeError('실제 Slack·VM 작업 미완료: '+task['status']+' '+task.get('error',''))
    with sqlite3.connect(ROOT/'data/state.sqlite3') as c:
        c.row_factory=sqlite3.Row
        receipts=[dict(r) for r in c.execute('SELECT sender,recipient,channel,ts,received_event,images_ready,received_images FROM slack_direct WHERE task_id=?',(tid,))]
    if not receipts or any(not r['received_event'] or not r['images_ready'] for r in receipts):raise RuntimeError('실제 상대 팀원의 DM 수신 확인 누락')
    if not any(json.loads(r['received_images']) for r in receipts):raise RuntimeError('실제 상대 팀원의 이미지 수신 확인 누락')
    with urllib.request.urlopen('http://127.0.0.1:8765/api/artifact?task='+tid+'&file=artifacts.zip',timeout=15) as response:bundle=response.read()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        name=next(n for n in archive.namelist() if n.endswith('guest-workspace.tar.gz'))
        with tarfile.open(fileobj=io.BytesIO(archive.read(name)),mode='r:gz') as guest:
            member=next(m for m in guest if m.name.lstrip('./')=='ready.txt')
            if guest.extractfile(member).read()!=b'TEAM_DM_READY\n':raise RuntimeError('실제 VM 파일 대조 실패')
    record={**status,'status':'passed','task_id':tid,'elapsed_seconds':round(time.time()-started,2),'receipts':receipts,'verification':json.loads(task['verification'])}
    (ROOT/'reports/slack-vm-implementation/slack-team-live-status.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':main()
