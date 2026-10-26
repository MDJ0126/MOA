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
import slack_workflow
import slack_direct
from slack_media import receive_images,upload_images

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'runtime'/'pythonlibs'))
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
    checks=[];identities=[];clients=[]
    for connection in config['connections']:
        secret=values[connection['id']]
        client=factory(secret['bot_token'])
        try:
            identity=client.auth_test()
            if not identity.get('ok'):raise ValueError('봇 인증 실패')
            identities.append(identity);clients.append(client)
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
                result=factory(secret['app_token']).apps_connections_open(app_token=secret['app_token'])
                if not result.get('url'):raise ValueError('Socket Mode 연결 주소를 받지 못했습니다.')
            checks.append({'id':connection['id'],'ok':True,'workspace':identity.get('team'),'bot_user_id':identity.get('user_id'),
                           'channel':channel_info.get('name') if channel_info else None})
        except ValueError:raise
        except Exception as e:raise ValueError('Slack 연결 확인 실패: '+safe_error(e)) from None
    if config.get('team_dm'):
        if len({x.get('team_id') for x in identities})!=1:raise ValueError('모든 팀원 봇을 같은 Slack 워크스페이스에 설치하세요.')
        if len({x['user_id'] for x in identities})!=len(identities):raise ValueError('팀원마다 별도 Slack 봇 계정이 필요합니다.')
        try:
            for i,client in enumerate(clients):
                for j,target in enumerate(identities):
                    if i==j:continue
                    result=client.conversations_open(users=target['user_id'])
                    if not result.get('channel',{}).get('id','').startswith('D'):raise ValueError('팀원 DM 생성 실패')
        except Exception as e:
            if getattr(e,'response',{}).get('error')=='cannot_dm_bot':
                write_setting(store,'slack_dm_capability',{'state':'unsupported','error':'cannot_dm_bot','checked_at':time.time()})
                raise ValueError('Slack이 봇끼리의 1:1 DM을 허용하지 않습니다(cannot_dm_bot). 계정 인증과 봇 등록은 완료됐으며 채널 방식으로 자동 대체하지 않았습니다.') from None
            raise ValueError('팀원 간 DM을 열지 못했습니다: '+safe_error(e)+' · im:write 권한과 봇별 설치를 확인하세요. 채널 대화로 대체하지 않습니다.') from None
    return {'ok':True,'checks':checks,'note':'인증·채널·Socket Mode 권한 확인 완료. 팀원 DM 모드는 양방향 대화방 열기도 확인합니다. 테스트 메시지는 보내지 않았습니다.'}

class SlackService:
    def __init__(self,store):
        slack_workflow.initialize(store)
        self.store=store;self.stop=threading.Event();self.clients=[];self.web={};self.config={};self.fingerprint=None
        self.status={'state':'disabled','message':'Slack을 연결하면 이곳에 상태가 표시됩니다.','connections':[]}
        self.receive_lock=threading.RLock()
        self.bot_user_ids={}
        self.bot_ids={}
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS slack_events(id TEXT PRIMARY KEY,created REAL)')
            c.execute("CREATE TABLE IF NOT EXISTS slack_inbox(id TEXT PRIMARY KEY,payload TEXT,connection_id TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,error TEXT DEFAULT '')")
            c.execute('CREATE TABLE IF NOT EXISTS slack_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            c.execute("INSERT OR IGNORE INTO slack_metadata VALUES('delivery_namespace',?)",(str(uuid.uuid4()),))
            self.delivery_namespace=uuid.UUID(c.execute("SELECT value FROM slack_metadata WHERE key='delivery_namespace'").fetchone()[0])
            c.execute('CREATE TABLE IF NOT EXISTS slack_recovery_audit(seq INTEGER PRIMARY KEY,created REAL,action TEXT,items TEXT)')

    def recovery(self):
        items=[];counts={}
        with self.store.connect() as c:
            kinds=[('notification','outbox'),('instruction','slack_inbox')]
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='slack_conversations'").fetchone():kinds.append(('conversation','slack_conversations'))
            for kind,table in kinds:
                counts[kind]=c.execute(f"SELECT COUNT(*) FROM {table} WHERE state='held_restored'").fetchone()[0]
                for row in c.execute(f"SELECT id,payload FROM {table} WHERE state='held_restored' ORDER BY rowid LIMIT 20"):
                    payload=json.loads(row['payload'])
                    text=payload.get('event',{}).get('text','') if kind!='notification' else payload.get('content',{}).get('summary','')
                    context=payload.get('event',{}).get('channel','') if kind!='notification' else ' · '.join(str(payload.get(key,'')) for key in ('task_id','sender','recipient'))
                    items.append({'kind':kind,'id':row['id'],'text':str(text),'context':context})
        return {'counts':counts,'items':items}

    def recover(self,action,items):
        if action not in ('resume','dismiss') or not isinstance(items,list) or not 1<=len(items)<=40:raise ValueError('처리할 복원 항목을 1~40개 선택하세요.')
        selected=[]
        for item in items:
            if not isinstance(item,dict) or item.get('kind') not in ('notification','instruction','conversation'):raise ValueError('복원 항목 종류 오류')
            kind=item['kind'];identity=item.get('id')
            if (kind=='notification' and (type(identity) is not int or identity<1)) or (kind!='notification' and (not isinstance(identity,str) or not identity or len(identity)>512)):raise ValueError('복원 항목 ID 오류')
            pair=(kind,identity)
            if pair in selected:raise ValueError('중복 복원 항목입니다.')
            selected.append(pair)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for kind,identity in selected:
                table={'notification':'outbox','instruction':'slack_inbox','conversation':'slack_conversations'}[kind]
                if kind=='conversation' and not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='slack_conversations'").fetchone():raise ValueError('복원된 대화 기록이 없습니다.')
                if not c.execute(f"SELECT 1 FROM {table} WHERE id=? AND state='held_restored'",(identity,)).fetchone():raise ValueError('선택 항목이 이미 처리되었거나 변경되었습니다. 목록을 새로 확인하세요.')
            for kind,identity in selected:
                table={'notification':'outbox','instruction':'slack_inbox','conversation':'slack_conversations'}[kind]
                if action=='resume':
                    extra=',available_at=0' if kind=='notification' else ''

                    if kind=='conversation':c.execute("UPDATE slack_conversations SET state=CASE WHEN decision LIKE '%control_answer%' THEN 'control_ready' ELSE 'received' END,error='' WHERE id=?",(identity,))
                    else:c.execute(f"UPDATE {table} SET state='pending',attempts=0,error=''{extra} WHERE id=?",(identity,))
                else:c.execute(f"UPDATE {table} SET state='dismissed' WHERE id=?",(identity,))
            c.execute('INSERT INTO slack_recovery_audit(created,action,items) VALUES(?,?,?)',(time.time(),action,json.dumps(items,ensure_ascii=False)))
        return {'processed':len(selected),'action':action}

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
        self.clients=[];self.web={};self.bot_user_ids={};self.bot_ids={}

    def configure(self,config):
        self.close_clients();self.config=config
        if not config['enabled']:
            self.status={'state':'disabled','message':'아직 연결하지 않았습니다.','connections':[]};return
        if not SDK_AVAILABLE:raise RuntimeError('Slack SDK가 설치되지 않았습니다.')
        rows=[];workspace_ids=set()
        for connection in config['connections']:
            client=WebClient(token=connection['bot_token'],timeout=10)
            identity=client.auth_test()
            workspace_ids.add(identity.get('team_id'))
            if config.get('team_dm') and identity['user_id'] in self.bot_user_ids.values():raise ValueError('팀원마다 서로 다른 봇 계정을 연결하세요.')
            self.bot_user_ids[connection['id']]=identity['user_id']
            self.bot_ids[connection['id']]=identity.get('bot_id','')
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
        if config.get('team_dm') and len(workspace_ids)!=1:raise ValueError('팀원 봇은 같은 워크스페이스에 설치해야 합니다.')
        self.status={'state':'connected','message':'업무 지시와 결과를 주고받을 준비가 되었습니다.' if config['receive'] else '결과 보고를 보낼 준비가 되었습니다.','connections':rows}

    def receive(self,payload,connection):
        with self.receive_lock:
            source=payload.get('event_id')
            if not source:return None
            with self.store.connect() as c:
                if c.execute('SELECT id FROM slack_events WHERE id=?',(source,)).fetchone():return None
            if slack_direct.capture_event(self,payload,connection):
                with self.store.connect() as c:c.execute('INSERT OR IGNORE INTO slack_events VALUES(?,?)',(source,time.time()))
                return None
            result=self._receive(payload,connection)
            if result and payload.get('event',{}).get('user'):
                with self.store.connect() as c:c.execute('INSERT OR IGNORE INTO slack_requesters VALUES(?,?)',(result,payload['event']['user']))
            if result and payload.get('event',{}).get('files'):
                client=self.web.get(connection['id'])
                if client is None:raise ValueError('이미지를 받을 Slack 연결이 없습니다.')
                images=receive_images(self.store,client,source,result,payload['event']['files'])
                self.store.log(result,'user','attachment',json.dumps({'images':images,'visual_understanding':False},ensure_ascii=False))
            with self.store.connect() as c:c.execute('INSERT OR IGNORE INTO slack_events VALUES(?,?)',(source,time.time()))
            return result

    def _receive(self,payload,connection):
        event=payload.get('event',{})
        # Managed bot publications are consumed from durable API receipts, not
        # echoed back as new instructions. This also blocks foreign bot loops.
        if event.get('bot_id') or event.get('subtype') not in (None,'file_share','thread_broadcast') or event.get('type') not in ('app_mention','message'):return None
        router=getattr(self,'conversations',None)
        if router:
            if event.get('user') not in self.config.get('allowed_users',[]):return None
            channel=event.get('channel','')
            if not channel.startswith('D') and channel!=self.config.get('channel'):return None
            return router.receive(payload,connection)
        if event.get('type')=='message' and event.get('channel_type')!='im':
            if not self.config.get('collaboration') or not event.get('thread_ts'):return None
            with self.store.connect() as c:
                if not c.execute('SELECT 1 FROM slack_threads WHERE channel=? AND thread_ts=?',(event.get('channel'),event['thread_ts'])).fetchone():return None
        if event.get('user') not in self.config.get('allowed_users',[]):return None
        channel=event.get('channel','')
        if not channel.startswith('D') and channel!=self.config.get('channel'):return None
        text=re.sub(r'<@[A-Z0-9]+>','',event.get('text','')).strip()
        if event.get('files'):text+=( '\n' if text else '')+'[첨부 이미지: '+', '.join(str(f.get('id','')) for f in event['files'] if isinstance(f,dict))+']; 이미지 내용은 직접 확인 전 미확인입니다.'
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
        if text in ('중지','일시중지','pause','계속','resume','상태','status','취소','cancel'):
            return self._control_event(source,channel,thread,text)
        with self.store.connect() as c:
            existing=slack_direct.task_in_thread(c,channel,thread)
        if existing and existing['status'] in ('queued','running'):
            recipient=connection['agent_id'] if connection['agent_id']!='shared' else 'developer'
            self.store.message(existing['id'],recipient,text,request_key=instruction_key)
            return existing['id']
        # Publish the task, assigned actor, and Slack thread in one commit so the
        # worker cannot claim it between creation and the actor/thread update.
        tid=self.store.add(text,'llama',max_revisions=team_settings(self.store)['max_revisions'],source_key='slack:'+source,
            environment='vm' if self.config.get('collaboration') else 'local',
            slack_context={'channel':channel,'thread_ts':thread,'connection_id':connection['id'],'agent_id':connection['agent_id']})
        self.store.deliver(tid,'system','user',{'summary':'작업을 접수했습니다. 작업 ID: '+tid})
        return tid

    def _control_event(self,source,channel,thread,text):
        # Serialize the current task state, control effect, response and receipt.
        # A failed receipt rolls back everything; another receiver sees the receipt.
        with self.store._write_transaction() as c:
            if c.execute('SELECT 1 FROM slack_events WHERE id=?',(source,)).fetchone():return None
            existing=slack_direct.task_in_thread(c,channel,thread)
            tid=existing['id'] if existing else None
            if text in ('중지','일시중지','pause','계속','resume'):
                action='pause' if text in ('중지','일시중지','pause') else 'resume'
                self.store.control(action,_connection=c)
                summary='작업 관리자 '+('일시 중지' if action=='pause' else '재개')
            elif existing:
                status=existing['status']
                if text in ('취소','cancel') and status in ('queued','running','failed','interrupted'):
                    self.store.control('cancel',tid,_connection=c);status='cancelled'
                summary='현재 작업 상태: '+status
            if existing:self.store.deliver(tid,'system','user',{'summary':summary},_connection=c)
            c.execute('INSERT INTO slack_events VALUES(?,?)',(source,time.time()))
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
                if content.get('result_text'):text+='\n\n'+str(content['result_text'])[:12000]
                for label,key in (('계획','steps'),('완료 조건','criteria')):
                    if content.get(key):text+='\n'+label+':\n'+'\n'.join('- '+str(item) for item in content[key])
                for item in content.get('guest_results',[])[-3:]:
                    text+='\nVM 명령: '+str(item.get('command',''))+'\n종료 코드: '+str(item.get('exit_code'))+'\n'+str(item.get('output',''))[:700]
                channel,thread=slack_direct.user_destination(self,row,connection)
                # Teammate handoffs are actual shared-channel messages. Human
                # replies still use each named bot's own DM with the requester.
                if row['channel'].startswith('D') and plan.get('collaboration',{}).get('transport')=='slack' and not plan['collaboration'].get('team_dm') and payload['recipient'] in {a['id'] for a in plan['agents']}:
                    channel=self.config['channel'];thread=''
                if not thread:text='*'+row['title'][:200]+'*\n'+text
                # Prevent model/user text from turning into Slack mass mentions.
                text=text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
                verification=content.get('verification') or {}
                if verification.get('tests_run') is not None:text+=f" · 테스트 {verification['tests_run']}개 · {'통과' if verification.get('passed') else '확인 필요'}"
                if not slack_direct.send(self,row,payload,text):continue
                with self.store.connect() as c:posted=c.execute('SELECT ts FROM slack_turns WHERE delivery_id=?',(row['delivery_id'],)).fetchone()
                if posted:result={'ts':posted[0]}
                else:
                    result=self.web[connection['id']].chat_postMessage(channel=channel,thread_ts=thread or None,text=text[:39000],
                        client_msg_id=str(uuid.uuid5(self.delivery_namespace,'local-agent-delivery:'+str(row['id']))),unfurl_links=False,unfurl_media=False)
                    if not result.get('ts'):raise ValueError('Slack 메시지 확인 ID가 없습니다.')
                    slack_workflow.record(self.store,row,payload,result['ts'])
                if not thread:
                    thread=result['ts']
                    with self.store.connect() as c:
                        if channel==row['channel']:c.execute('UPDATE slack_threads SET thread_ts=? WHERE task_id=?',(thread,payload['task_id']))
                        else:c.execute('UPDATE slack_user_dm SET thread_ts=? WHERE task_id=? AND connection_id=?',(thread,payload['task_id'],connection['id']))
                self._sending_client=self.web[connection['id']]
                self._sending_connection=connection['id']
                destination=dict(row);destination['channel']=channel
                upload_images(self,destination,payload,thread)
                if not slack_direct.channel_receipt(self,row,payload,channel,result['ts']):continue
                if channel.startswith('D'):slack_direct.mirror(self,row,payload,text,connection)
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
