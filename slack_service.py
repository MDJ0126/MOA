"""Opt-in Slack transport. Credentials remain in DPAPI storage, never in API responses."""
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time
import uuid
from setup_config import slack_settings,prepare_slack,write_setting,team_settings

sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime'/'pythonlibs'))
try:
    from slack_sdk.web import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse
    SDK_AVAILABLE=True
except ImportError:
    SDK_AVAILABLE=False

def safe_error(error):
    response=getattr(error,'response',None)
    return str(response.get('error','slack_error')) if response is not None else type(error).__name__

def test_connection(store,value,client_factory=None):
    config,values=prepare_slack(store,{**value,'enabled':True})
    if not SDK_AVAILABLE and client_factory is None:raise ValueError('Slack 연결 도구를 설치해 주세요. 문서의 설치 안내를 확인하세요.')
    factory=client_factory or (lambda token:WebClient(token=token,timeout=10))
    checks=[]
    for connection in config['connections']:
        secret=values[connection['id']]
        client=factory(secret['bot_token'])
        try:
            identity=client.auth_test()
            if not identity.get('ok'):raise ValueError('봇 인증 실패')
            channel_info=None
            if config['channel']:
                channel_info=client.conversations_info(channel=config['channel'])['channel']
                member=False;cursor=None
                for _ in range(50):
                    members=client.conversations_members(channel=config['channel'],limit=200,cursor=cursor)
                    if identity['user_id'] in members.get('members',[]):member=True;break
                    cursor=members.get('response_metadata',{}).get('next_cursor')
                    if not cursor:break
                if not member:raise ValueError('채널에서 봇 참여를 확인하지 못했습니다. 봇을 초대하거나 채널 ID를 확인해 주세요.')
            if config['receive']:
                result=factory(secret['app_token']).apps_connections_open()
                if not result.get('url'):raise ValueError('Socket Mode 연결 주소를 받지 못했습니다.')
            checks.append({'id':connection['id'],'ok':True,'workspace':identity.get('team'),'bot_user_id':identity.get('user_id'),
                           'channel':channel_info.get('name') if channel_info else None})
        except ValueError:raise
        except Exception as e:raise ValueError('Slack 연결 확인 실패: '+safe_error(e)) from None
    return {'ok':True,'checks':checks,'note':'인증·채널·Socket Mode 권한 확인 완료. 테스트 메시지는 보내지 않았습니다.'}

class SlackService:
    def __init__(self,store):
        self.store=store;self.stop=threading.Event();self.clients=[];self.web={};self.config={};self.fingerprint=None
        self.status={'state':'disabled','message':'Slack을 연결하면 이곳에 상태가 표시됩니다.','connections':[]}
        self.receive_lock=threading.RLock()
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS slack_events(id TEXT PRIMARY KEY,created REAL)')
            c.execute("CREATE TABLE IF NOT EXISTS slack_inbox(id TEXT PRIMARY KEY,payload TEXT,connection_id TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,error TEXT DEFAULT '')")
            c.execute('CREATE TABLE IF NOT EXISTS slack_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            c.execute("INSERT OR IGNORE INTO slack_metadata VALUES('delivery_namespace',?)",(str(uuid.uuid4()),))
            self.delivery_namespace=uuid.UUID(c.execute("SELECT value FROM slack_metadata WHERE key='delivery_namespace'").fetchone()[0])

    def enqueue(self,payload,connection):
        event_id=payload.get('event_id')
        if not event_id:return
        with self.store.connect() as c:c.execute('INSERT OR IGNORE INTO slack_inbox(id,payload,connection_id) VALUES(?,?,?)',(event_id,json.dumps(payload,ensure_ascii=False),connection['id']))

    def drain(self):
        with self.store.connect() as c:rows=c.execute("SELECT * FROM slack_inbox WHERE state='pending' AND attempts<5 ORDER BY rowid LIMIT 10").fetchall()
        for row in rows:
            connection=next((x for x in self.config['connections'] if x['id']==row['connection_id']),None)
            if connection is None:continue
            try:
                self.receive(json.loads(row['payload']),connection)
                with self.store.connect() as c:c.execute("UPDATE slack_inbox SET state='done' WHERE id=?",(row['id'],))
            except Exception as e:
                with self.store.connect() as c:c.execute('UPDATE slack_inbox SET attempts=attempts+1,error=? WHERE id=?',(safe_error(e),row['id']))

    def close_clients(self):
        for client in self.clients:
            try:client.close()
            except Exception:pass
        self.clients=[];self.web={}

    def configure(self,config):
        self.close_clients();self.config=config
        if not config['enabled']:
            self.status={'state':'disabled','message':'아직 연결하지 않았습니다.','connections':[]};return
        if not SDK_AVAILABLE:raise RuntimeError('Slack SDK가 설치되지 않았습니다.')
        rows=[]
        for connection in config['connections']:
            client=WebClient(token=connection['bot_token'],timeout=10)
            identity=client.auth_test()
            self.web[connection['id']]=client
            row={'id':connection['id'],'agent_id':connection['agent_id'],'workspace':identity.get('team'),'state':'connected'}
            if config['receive']:
                socket=SocketModeClient(app_token=connection['app_token'],web_client=client)
                def listener(socket,request,connection=connection):
                    if request.type!='events_api':return
                    try:
                        self.enqueue(request.payload,connection)
                        socket.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
                    except Exception as e:
                        self.status['message']='메시지 처리 오류: '+safe_error(e)
                socket.socket_mode_request_listeners.append(listener)
                socket.connect();self.clients.append(socket)
            rows.append(row)
        self.status={'state':'connected','message':'업무 지시와 결과를 주고받을 준비가 되었습니다.' if config['receive'] else '결과 보고를 보낼 준비가 되었습니다.','connections':rows}

    def receive(self,payload,connection):
        with self.receive_lock:
            source=payload.get('event_id')
            if not source:return None
            with self.store.connect() as c:
                if c.execute('SELECT id FROM slack_events WHERE id=?',(source,)).fetchone():return None
            result=self._receive(payload,connection)
            with self.store.connect() as c:c.execute('INSERT OR IGNORE INTO slack_events VALUES(?,?)',(source,time.time()))
            return result

    def _receive(self,payload,connection):
        event=payload.get('event',{})
        if event.get('bot_id') or event.get('subtype') or event.get('type') not in ('app_mention','message'):return None
        if event.get('type')=='message' and event.get('channel_type')!='im':return None
        if event.get('user') not in self.config.get('allowed_users',[]):return None
        channel=event.get('channel','')
        if not channel.startswith('D') and channel!=self.config.get('channel'):return None
        text=re.sub(r'<@[A-Z0-9]+>','',event.get('text','')).strip()
        if not text:return None
        source=payload.get('event_id')
        if not source:return None
        thread=event.get('thread_ts') or event.get('ts')
        if not thread:return None
        instruction_key='slack_'+hashlib.sha256(source.encode('utf-8')).hexdigest()
        with self.store.connect() as c:
            # A committed instruction can outlive the event acknowledgement and
            # the task can finish before replay. Check its receipt before routing.
            receipt=c.execute('SELECT d.task_id FROM message_requests r JOIN deliveries d ON d.id=r.delivery_id WHERE r.request_key=?',(instruction_key,)).fetchone()
            if receipt:return receipt['task_id']
            prior=c.execute('SELECT id FROM tasks WHERE source_key=?',('slack:'+source,)).fetchone()
            if prior:
                c.execute('INSERT OR IGNORE INTO slack_threads VALUES(?,?,?,?)',(prior['id'],channel,thread,connection['id']))
                return prior['id']
        with self.store.connect() as c:
            existing=c.execute('SELECT t.id,t.status FROM slack_threads s JOIN tasks t ON t.id=s.task_id WHERE s.channel=? AND s.thread_ts=? ORDER BY t.created DESC LIMIT 1',(channel,thread)).fetchone()
        if text in ('중지','일시중지','pause','계속','resume'):
            action='pause' if text in ('중지','일시중지','pause') else 'resume'
            self.store.control(action)
            if existing:self.store.deliver(existing['id'],'system','user',{'summary':'작업 관리자 '+('일시 중지' if action=='pause' else '재개')})
            return existing['id'] if existing else None
        if existing and text in ('상태','status','취소','cancel'):
            status=existing['status']
            if text in ('취소','cancel') and status in ('queued','running','failed','interrupted'):
                self.store.control('cancel',existing['id']);status='cancelled'
            self.store.deliver(existing['id'],'system','user',{'summary':'현재 작업 상태: '+status})
            return existing['id']
        if existing and existing['status'] in ('queued','running'):
            recipient=connection['agent_id'] if connection['agent_id']!='shared' else 'developer'
            self.store.message(existing['id'],recipient,text,request_key=instruction_key)
            return existing['id']
        # Publish the task, assigned actor, and Slack thread in one commit so the
        # worker cannot claim it between creation and the actor/thread update.
        tid=self.store.add(text,'llama',max_revisions=team_settings(self.store)['max_revisions'],source_key='slack:'+source,
            slack_context={'channel':channel,'thread_ts':thread,'connection_id':connection['id'],'agent_id':connection['agent_id']})
        self.store.deliver(tid,'system','user',{'summary':'작업을 접수했습니다. 작업 ID: '+tid})
        return tid

    def flush(self):
        if not self.config.get('notify'):return
        # Ignore old local history: only mapped Slack tasks and deliveries after connection setup.
        with self.store.connect() as c:
            rows=c.execute("SELECT o.*,s.channel,s.thread_ts,s.connection_id,t.execution_plan,t.title FROM outbox o JOIN deliveries d ON d.id=o.delivery_id JOIN slack_threads s ON s.task_id=d.task_id JOIN tasks t ON t.id=d.task_id WHERE o.state='pending' AND o.available_at<=? ORDER BY o.id LIMIT 20",(time.time(),)).fetchall()
        for row in rows:
            payload=json.loads(row['payload']);sender=payload['sender'];plan=json.loads(row['execution_plan'])
            names={a['id']:a['name'] for a in plan['agents']};names.update(system='작업 관리자',user='사용자')
            connection=next((c for c in self.config['connections'] if c['agent_id']==sender),None)
            if connection is None:connection=next((c for c in self.config['connections'] if c['id']==row['connection_id']),self.config['connections'][0])
            try:
                content=payload['content'];text=f"*{names.get(sender,sender)} → {names.get(payload['recipient'],payload['recipient'])}*\n{content.get('summary','')}\n`{payload['task_id']}`"
                with self.store.connect() as c:thread=c.execute('SELECT thread_ts FROM slack_threads WHERE task_id=?',(payload['task_id'],)).fetchone()[0]
                if not thread:text='*'+row['title'][:200]+'*\n'+text
                verification=content.get('verification') or {}
                if verification.get('tests_run') is not None:text+=f" · 테스트 {verification['tests_run']}개 · {'통과' if verification.get('passed') else '확인 필요'}"
                result=self.web[connection['id']].chat_postMessage(channel=row['channel'],thread_ts=thread or None,text=text[:3500],
                    client_msg_id=str(uuid.uuid5(self.delivery_namespace,'local-agent-delivery:'+str(row['id']))),unfurl_links=False,unfurl_media=False)
                with self.store.connect() as c:
                    c.execute("UPDATE outbox SET state='sent',receipt=? WHERE id=?",(result.get('ts',''),row['id']))
                    if not thread:c.execute('UPDATE slack_threads SET thread_ts=? WHERE task_id=?',(result['ts'],payload['task_id']))
            except Exception as e:
                with self.store.connect() as c:c.execute('UPDATE outbox SET attempts=attempts+1,error=?,available_at=? WHERE id=?',(safe_error(e),time.time()+min(300,5*2**min(row['attempts'],6)),row['id']))
                self.status['message']='전송 재시도 대기: '+safe_error(e)

    def loop(self):
        next_retry=0
        while not self.stop.is_set():
            try:
                config=slack_settings(self.store,True)
                fingerprint=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
                if fingerprint!=self.fingerprint and time.time()>=next_retry:
                    self.configure(config);self.fingerprint=fingerprint
                if self.config.get('enabled') and fingerprint==self.fingerprint:
                    connected=all(client.is_connected() for client in self.clients)
                    self.status['state']='connected' if connected else 'reconnecting'
                    if not connected:self.status['message']='Slack에 다시 연결하고 있습니다.'
                    self.drain();self.flush()
            except Exception as e:
                self.status={'state':'error','message':'연결을 다시 확인해 주세요: '+safe_error(e),'connections':[]};next_retry=time.time()+15
            self.stop.wait(1)
        self.close_clients()
