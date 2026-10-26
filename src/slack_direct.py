"""Agent-to-agent Slack DMs with recipient-side event and file receipts."""
import json
import time
import uuid
from slack_media import upload_images, receive_images


def initialize(store):
    with store.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS slack_direct(
            delivery_id INTEGER PRIMARY KEY,task_id TEXT,sender TEXT,recipient TEXT,
            sender_connection TEXT,recipient_connection TEXT,sender_user TEXT,
            channel TEXT,ts TEXT,received_event TEXT DEFAULT '',images_ready INTEGER DEFAULT 0,
            received_images TEXT DEFAULT '[]',created REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS slack_user_dm(
            task_id TEXT,connection_id TEXT,channel TEXT,thread_ts TEXT DEFAULT '',
            PRIMARY KEY(task_id,connection_id))''')
        c.execute('CREATE TABLE IF NOT EXISTS slack_requesters(task_id TEXT PRIMARY KEY,user_id TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS slack_mirrors(delivery_id INTEGER PRIMARY KEY,channel TEXT,ts TEXT)')


def capture_event(service,payload,connection):
    """Only acknowledge an exact outstanding publication from our known sender."""
    event=payload.get('event',{})
    if event.get('type')!='message' or event.get('channel_type') not in ('im','channel','group'):return False
    if event.get('subtype') not in (None,'bot_message'):return False
    sender_user=event.get('user')
    if not sender_user and event.get('bot_id'):
        source=next((cid for cid,bid in service.bot_ids.items() if bid==event['bot_id']),None)
        sender_user=service.bot_user_ids.get(source)
    with service.store.connect() as c:
        row=c.execute('''SELECT * FROM slack_direct WHERE recipient_connection=?
            AND channel=? AND ts=? AND sender_user=?''',
            (connection['id'],event.get('channel'),event.get('ts'),sender_user)).fetchone()
        if not row and event.get('client_msg_id'):
            for candidate in c.execute("SELECT * FROM slack_direct WHERE recipient_connection=? AND channel=? AND sender_user=? AND ts=''",
                                       (connection['id'],event.get('channel'),sender_user)):
                expected=str(uuid.uuid5(service.delivery_namespace,'team-dm:'+str(candidate['delivery_id'])))
                if event['client_msg_id']==expected:
                    c.execute('UPDATE slack_direct SET ts=? WHERE delivery_id=?',(event['ts'],candidate['delivery_id']))
                    row=candidate;break
        if not row:return False
        c.execute("UPDATE slack_direct SET received_event=? WHERE delivery_id=? AND received_event=''",
                  (payload['event_id'],row['delivery_id']))
    service.store.log(row['task_id'],row['recipient'],'slack_dm_received' if row['channel'].startswith('D') else 'slack_channel_received',
                      json.dumps({'sender':row['sender'],'channel':row['channel'],'ts':row['ts']},ensure_ascii=False))
    return True


def task_in_thread(c,channel,thread):
    return c.execute('''SELECT t.id,t.status FROM tasks t WHERE t.id IN (
        SELECT task_id FROM slack_threads WHERE channel=? AND thread_ts=?
        UNION SELECT task_id FROM slack_user_dm WHERE channel=? AND thread_ts=?)
        ORDER BY t.created DESC LIMIT 1''',(channel,thread,channel,thread)).fetchone()


def send(service,row,payload,text):
    """Send using the sender's identity, then wait for the recipient's socket event."""
    plan=json.loads(row['execution_plan'])
    if not plan.get('collaboration',{}).get('team_dm'):return True
    agents={a['id'] for a in plan['agents']}
    sender,recipient=payload['sender'],payload['recipient']
    if sender not in agents or recipient not in agents or sender==recipient:return True
    connections={x['agent_id']:x for x in service.config['connections'] if x['agent_id']!='shared'}
    if sender not in connections or recipient not in connections:
        raise ValueError('팀원 간 DM에는 각 팀원의 별도 봇 연결이 필요합니다.')
    source,target=connections[sender],connections[recipient]
    if source['id'] not in service.bot_user_ids or target['id'] not in service.bot_user_ids:
        raise ValueError('DM 팀원 프로필 인증을 확인해 주세요.')
    with service.store.connect() as c:
        prior=c.execute('SELECT * FROM slack_direct WHERE delivery_id=?',(row['delivery_id'],)).fetchone()
    if not prior:
        channel=service.web[source['id']].conversations_open(users=service.bot_user_ids[target['id']])['channel']['id']
        if not channel.startswith('D'):raise ValueError('Slack에서 팀원 간 1:1 DM을 열지 못했습니다.')
        with service.store.connect() as c:
            c.execute('''INSERT INTO slack_direct(delivery_id,task_id,sender,recipient,sender_connection,
                recipient_connection,sender_user,channel,ts,created) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (row['delivery_id'],payload['task_id'],sender,recipient,source['id'],target['id'],
                 service.bot_user_ids[source['id']],channel,'',time.time()))
        # Persist the intent before any side effect. A lost reply is reconciled,
        # never blindly reposted after a restart.
        result=service.web[source['id']].chat_postMessage(channel=channel,text=text[:39000],
            client_msg_id=str(uuid.uuid5(service.delivery_namespace,'team-dm:'+str(row['delivery_id']))),
            unfurl_links=False,unfurl_media=False)
        if not result.get('ts'):raise ValueError('팀원 DM 전송 확인 ID가 없습니다.')
        with service.store.connect() as c:
            c.execute('UPDATE slack_direct SET ts=? WHERE delivery_id=?',(result['ts'],row['delivery_id']))
            prior=c.execute('SELECT * FROM slack_direct WHERE delivery_id=?',(row['delivery_id'],)).fetchone()
    if not prior['ts']:
        message_id=str(uuid.uuid5(service.delivery_namespace,'team-dm:'+str(row['delivery_id'])))
        listing=service.web[prior['sender_connection']].conversations_history(channel=prior['channel'],oldest=str(prior['created']-5),limit=100)
        match=next((m for m in listing.get('messages',[]) if m.get('client_msg_id')==message_id and m.get('user')==prior['sender_user']),None)
        if not match:raise ValueError('이전 팀원 DM 전송 결과가 미확인입니다. 중복 발송을 막기 위해 수동 확인이 필요합니다.')
        with service.store.connect() as c:
            c.execute('UPDATE slack_direct SET ts=? WHERE delivery_id=?',(match['ts'],row['delivery_id']))
            prior=c.execute('SELECT * FROM slack_direct WHERE delivery_id=?',(row['delivery_id'],)).fetchone()
    service._sending_client=service.web[prior['sender_connection']]
    service._sending_connection=prior['sender_connection']
    dm_row=dict(row);dm_row['channel']=prior['channel'];dm_row['upload_scope']='dm:'+prior['channel']
    upload_images(service,dm_row,payload,prior['ts'])
    return confirm(service,row,payload,prior,'dm:'+prior['channel']+'|')


def confirm(service,row,payload,prior,prefix=''):
    # This is an actual recipient event, not a local synthetic delivery.
    if not prior['received_event']:
        label='DM' if prior['channel'].startswith('D') else '채널 메시지'
        service.status['message']='팀원 '+label+' 수신 확인 대기 중입니다.' if time.time()-prior['created']<60 else '팀원 '+label+' 수신 미확인: 상대 봇의 Socket Mode와 메시지 이벤트 설정을 확인하세요. 다음 작업을 보류했습니다.'
        return False
    if not prior['images_ready']:
        with service.store.connect() as c:
            images=[{'id':r[0]} for r in c.execute("SELECT file_id FROM slack_uploads WHERE delivery_id=? AND state='sent' AND substr(path,1,?)=?",
                    (row['delivery_id'],len(prefix),prefix))]
        # Download with the recipient's own credentials to prove access.
        paths=[]
        for offset in range(0,len(images),4):
            paths.extend(receive_images(service.store,service.web[prior['recipient_connection']],
                         'team-dm:'+str(row['delivery_id']),payload['task_id'],images[offset:offset+4]))
        with service.store.connect() as c:
            c.execute('UPDATE slack_direct SET images_ready=1,received_images=? WHERE delivery_id=?',
                      (json.dumps(paths),row['delivery_id']))
        service.store.log(payload['task_id'],prior['recipient'],'slack_dm_images' if prior['channel'].startswith('D') else 'slack_channel_images',json.dumps({'images':paths,'visual_understanding':False}))
    return True


def channel_receipt(service,row,payload,channel,ts):
    plan=json.loads(row['execution_plan'])
    if not plan.get('collaboration',{}).get('channel_receipts') or not channel.startswith(('C','G')):return True
    connections={c['agent_id']:c for c in service.config['connections'] if c['agent_id']!='shared'}
    sender,recipient=payload['sender'],payload['recipient']
    if sender==recipient or sender not in connections or recipient not in connections:return True
    source,target=connections[sender],connections[recipient]
    with service.store.connect() as c:
        c.execute('''INSERT OR IGNORE INTO slack_direct(delivery_id,task_id,sender,recipient,sender_connection,
                  recipient_connection,sender_user,channel,ts,created) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                  (row['delivery_id'],payload['task_id'],sender,recipient,source['id'],target['id'],
                   service.bot_user_ids[source['id']],channel,ts,time.time()))
        prior=c.execute('SELECT * FROM slack_direct WHERE delivery_id=?',(row['delivery_id'],)).fetchone()
    return confirm(service,row,payload,prior)


def user_destination(service,row,connection):
    """A teammate cannot post in another teammate's private DM with the user."""
    with service.store.connect() as c:
        current=c.execute('SELECT thread_ts FROM slack_threads WHERE task_id=?',(json.loads(row['payload'])['task_id'],)).fetchone()
    thread=current[0] if current else row['thread_ts']
    if not row['channel'].startswith('D'):return row['channel'],thread
    with service.store.connect() as c:
        saved=c.execute('SELECT channel,thread_ts FROM slack_user_dm WHERE task_id=? AND connection_id=?',
                        (json.loads(row['payload'])['task_id'],connection['id'])).fetchone()
        if saved:return saved['channel'],saved['thread_ts']
        requester=c.execute('SELECT user_id FROM slack_requesters WHERE task_id=?',
                            (json.loads(row['payload'])['task_id'],)).fetchone()
    if connection['id']==row['connection_id']:return row['channel'],thread
    if not requester:raise ValueError('사용자 DM의 요청자 정보를 확인하지 못했습니다.')
    channel=service.web[connection['id']].conversations_open(users=requester['user_id'])['channel']['id']
    if not channel.startswith('D'):raise ValueError('사용자 DM 열기 실패')
    with service.store.connect() as c:c.execute('INSERT OR IGNORE INTO slack_user_dm(task_id,connection_id,channel) VALUES(?,?,?)',
                                               (json.loads(row['payload'])['task_id'],connection['id'],channel))
    return channel,''


def mirror(service,row,payload,text,connection):
    """The owner can observe private teammate exchanges in the reporting channel."""
    if not row['channel'].startswith('D') or json.loads(row['execution_plan']).get('collaboration',{}).get('transport')!='slack':return
    channel=service.config.get('channel')
    if not channel:raise ValueError('팀원 DM 기록을 볼 보고 채널이 필요합니다.')
    with service.store.connect() as c:prior=c.execute('SELECT * FROM slack_mirrors WHERE delivery_id=?',(row['delivery_id'],)).fetchone()
    if not prior:
        result=service.web[connection['id']].chat_postMessage(channel=channel,text=text[:39000],
            client_msg_id=str(uuid.uuid5(service.delivery_namespace,'dm-mirror:'+str(row['delivery_id']))),
            unfurl_links=False,unfurl_media=False)
        if not result.get('ts'):raise ValueError('DM 기록 공유 확인 ID가 없습니다.')
        with service.store.connect() as c:c.execute('INSERT INTO slack_mirrors VALUES(?,?,?)',(row['delivery_id'],channel,result['ts']))
        thread=result['ts']
    else:channel,thread=prior['channel'],prior['ts']
    destination=dict(row);destination.update(channel=channel,upload_scope='mirror:'+channel)
    upload_images(service,destination,payload,thread)
