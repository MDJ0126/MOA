"""Durable, responsive Slack conversation entrypoint; no VM job for greetings/questions."""
import hashlib,json,re,threading,time,uuid
from leader_inbox import leader
from organization import display_name
from setup_config import team_settings,read_setting
from workspace_resources import frozen_context
import slack_direct

class SlackConversations:
 def __init__(self,service,decider):
  self.service=service;self.store=service.store;self.decider=decider;self.stop=threading.Event();self.lock=threading.RLock()
  with self.store.connect() as c:
   c.execute("CREATE TABLE IF NOT EXISTS slack_conversations(id TEXT PRIMARY KEY,payload TEXT,connection_id TEXT,agent_id TEXT,channel TEXT,thread_ts TEXT,state TEXT,task_id TEXT DEFAULT '',decision TEXT DEFAULT '',error TEXT DEFAULT '',created REAL,updated REAL)")
   c.execute("CREATE TABLE IF NOT EXISTS slack_conversation_posts(request_id TEXT,kind TEXT,state TEXT,ts TEXT DEFAULT '',content TEXT,client_msg_id TEXT,PRIMARY KEY(request_id,kind))")
   c.execute("CREATE TABLE IF NOT EXISTS slack_conversation_attempts(id TEXT PRIMARY KEY,attempts INTEGER DEFAULT 0,retry_at REAL DEFAULT 0)")
   c.execute("UPDATE slack_conversations SET state='queued' WHERE state='planning'")
   c.execute("UPDATE slack_conversation_posts SET state='uncertain' WHERE state='posting'")
 def target(self,event,connection):
  agents=team_settings(self.store)['agents'];chief=leader(agents);config=self.service.config
  if event.get('channel_type')=='im' or event.get('channel','').startswith('D'):return connection
  mentions=re.findall(r'<@([A-Z0-9]+)>',event.get('text',''))
  targets=[c for c in config['connections'] if self.service.bot_user_ids.get(c['id']) in mentions]
  if targets:return next((c for c in targets if c['agent_id']==chief['id']),targets[0])
  return next((c for c in config['connections'] if c['agent_id']==chief['id']),next((c for c in config['connections'] if c['agent_id']=='shared'),config['connections'][0]))
 def receive(self,payload,connection):
  event=payload['event'];target=self.target(event,connection)
  if target['id']!=connection['id']:return None
  text=re.sub(r'<@[A-Z0-9]+>','',event.get('text','')).strip()
  if not text and not event.get('files'):return None
  canonical=hashlib.sha256(json.dumps([event.get('team',''),event.get('channel'),event.get('ts'),event.get('user')],separators=(',',':')).encode()).hexdigest()
  chief=leader(team_settings(self.store)['agents']);aid=target['agent_id'] if target['agent_id']!='shared' else chief['id'];now=time.time()
  with self.lock:
   with self.store.connect() as c:
    c.execute("INSERT OR IGNORE INTO slack_conversations(id,payload,connection_id,agent_id,channel,thread_ts,state,created,updated) VALUES(?,?,?,?,?,?,'received',?,?)",(canonical,json.dumps(payload,ensure_ascii=False),target['id'],aid,event['channel'],event.get('thread_ts') or event['ts'],now,now))
    row=dict(c.execute('SELECT * FROM slack_conversations WHERE id=?',(canonical,)).fetchone())
   if row['state'] in ('received','control_ready'):self.prepare(row)
  return None
 def set_state(self,jid,state,**fields):
  fields={**fields,'state':state,'updated':time.time()}
  with self.store.connect() as c:c.execute('UPDATE slack_conversations SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),jid))
 def send(self,row,kind,text):
  if not self.service.config.get('enabled') or not self.service.config.get('notify'):raise ValueError('Slack 응답 전송 설정이 꺼져 있습니다.')
  client=self.service.web.get(row['connection_id'])
  if client is None:raise ValueError('응답할 Slack 봇 연결이 준비되지 않았습니다.')
  mid=str(uuid.uuid5(self.service.delivery_namespace,'conversation:'+row['id']+':'+kind))
  escaped=text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
  with self.store.connect() as c:
   old=c.execute('SELECT * FROM slack_conversation_posts WHERE request_id=? AND kind=?',(row['id'],kind)).fetchone()
   if old and old['state']=='sent':return old['ts']
   if not old:c.execute("INSERT INTO slack_conversation_posts(request_id,kind,state,content,client_msg_id) VALUES(?,?,'pending',?,?)",(row['id'],kind,escaped,mid))
  if old and old['state'] in ('posting','uncertain'):
   result=client.conversations_replies(channel=row['channel'],ts=row['thread_ts'],limit=100)
   found=next((m for m in result.get('messages',[]) if m.get('client_msg_id')==mid),None)
   if not found:raise ValueError('이전 응답 전송 결과가 미확인입니다. 중복 전송 없이 스레드를 다시 확인합니다.')
   ts=found['ts']
  else:
   with self.store.connect() as c:c.execute("UPDATE slack_conversation_posts SET state='posting' WHERE request_id=? AND kind=?",(row['id'],kind))
   try:
    event=json.loads(row['payload'])['event']
    result=client.chat_postMessage(channel=row['channel'],thread_ts=row['thread_ts'],reply_broadcast=not row['channel'].startswith('D') and not event.get('thread_ts'),text=escaped[:35000],client_msg_id=mid,unfurl_links=False,unfurl_media=False)
    ts=result.get('ts')
    if not ts:raise ValueError('Slack 응답 확인 ID가 없습니다.')
   except Exception as e:
    state='pending' if getattr(e,'response',{}).get('error') else 'uncertain'
    with self.store.connect() as c:c.execute('UPDATE slack_conversation_posts SET state=? WHERE request_id=? AND kind=?',(state,row['id'],kind))
    raise
  with self.store.connect() as c:c.execute("UPDATE slack_conversation_posts SET state='sent',ts=? WHERE request_id=? AND kind=?",(ts,row['id'],kind))
  self.service.status['message']='Slack 메시지에 답변했습니다.';return ts
 def status_text(self,existing=None):
  with self.store.connect() as c:
   tasks=[dict(r) for r in c.execute("SELECT id,title,status,stage,execution_plan,error FROM tasks WHERE status IN ('queued','running','interrupted','failed') ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,created DESC LIMIT 5")]
  if existing:tasks=[dict(existing)]
  active=[t for t in tasks if t['status'] in ('running','queued')]
  if not existing and not active:return '현재 진행 중인 업무는 없습니다. 새 업무를 말씀해 주세요.'
  labels={'queued':'대기 중','running':'진행 중','interrupted':'확인 필요','failed':'확인 필요','completed':'완료','cancelled':'취소됨'};lines=[]
  for t in tasks if existing else active:
   plan=json.loads(t['execution_plan']);pipeline=plan.get('pipeline',[]);role=pipeline[t['stage']] if t['stage']<len(pipeline) else '';aid=plan.get('assignment',{}).get(role);agent=next((a for a in plan.get('agents',[]) if a['id']==aid),None)
   lines.append((display_name(agent)+' · ' if agent else '')+labels.get(t['status'],t['status'])+' · '+t['title'][:100]+(' · '+t.get('error','')[:160] if t.get('error') else ''))
  return '\n'.join(lines)
 def prepare(self,row):
  if row['state']=='control_ready':
   self.send(row,'reply',json.loads(row['decision'])['control_answer']);self.set_state(row['id'],'replied');return
  event=json.loads(row['payload'])['event'];text=re.sub(r'<@[A-Z0-9]+>','',event.get('text','')).strip();simple=re.sub(r'[\s!?.,~…]+','',text).lower()
  agents=team_settings(self.store)['agents'];agent=next((a for a in agents if a['id']==row['agent_id']),leader(agents));name=display_name(agent)
  with self.store.connect() as c:
   existing=slack_direct.task_in_thread(c,row['channel'],row['thread_ts'])
   if existing:existing=c.execute('SELECT * FROM tasks WHERE id=?',(existing['id'],)).fetchone()
  if simple in ('여러분','팀원들','계세요','여보세요','안녕','안녕하세요','안녕하세요여러분','하이','hello','hi','거기있어요','응답해','대답해') and not event.get('files'):
   self.send(row,'reply','네, 말씀하세요. '+name+'입니다. 어떤 일을 도와드릴까요?');self.set_state(row['id'],'replied');return
  if simple in ('상태','status','진행상황','지금뭐해','지금뭐해요','뭐하고있어','뭐하고있어요','어디까지됐어','어디까지됐어요','진행중이야','작업중이야'):
   self.send(row,'reply',self.status_text(existing));self.set_state(row['id'],'replied');return
  if simple in ('중지','일시중지','pause','계속','resume','취소','cancel'):
   with self.store._write_transaction() as c:
    if simple in ('중지','일시중지','pause','계속','resume'):
     action='pause' if simple in ('중지','일시중지','pause') else 'resume';self.store.control(action,_connection=c);answer='업무를 일시 중지했습니다.' if action=='pause' else '업무를 다시 진행하겠습니다.'
    elif existing and existing['status'] in ('queued','running','interrupted','failed'):self.store.control('cancel',existing['id'],_connection=c);answer='이 스레드의 업무를 취소했습니다.'
    else:answer='취소할 업무의 스레드에서 말씀해 주세요.'
    c.execute("UPDATE slack_conversations SET state='control_ready',decision=? WHERE id=?",(json.dumps({'control_answer':answer},ensure_ascii=False),row['id']))
   self.send(row,'reply',answer);self.set_state(row['id'],'replied');return
  if existing and existing['status'] in ('queued','running'):
   plan=json.loads(existing['execution_plan']);pipeline=plan['pipeline'];role=pipeline[min(existing['stage'],len(pipeline)-1)];recipient=plan['assignment'][role]
   self.store.message(existing['id'],recipient,text or '[첨부 이미지]',request_key='slack_'+row['id'])
   if event.get('files'):self.attach(existing['id'],row,event)
   self.send(row,'reply','추가 지시를 담당자에게 전달했습니다.\n'+self.status_text(existing));self.set_state(row['id'],'instructed',task_id=existing['id']);return
  self.send(row,'ack',name+'입니다. 말씀 확인했습니다. 내용을 확인해 답변드리겠습니다.'+(' 현재 업무는 일시 중지 상태이며 요청은 접수했습니다.' if read_setting(self.store,'paused',False) else ''))
  self.set_state(row['id'],'queued')
 def attach(self,tid,row,event):
  from slack_media import receive_images
  images=receive_images(self.store,self.service.web[row['connection_id']],json.loads(row['payload'])['event_id'],tid,event['files'])
  self.store.log(tid,'user','attachment',json.dumps({'images':images,'visual_understanding':False},ensure_ascii=False))
 def step(self):
  with self.lock:
   with self.store.connect() as c:found=c.execute("SELECT * FROM slack_conversations WHERE state IN ('received','queued','reply_pending','control_ready') AND NOT EXISTS (SELECT 1 FROM slack_conversation_attempts a WHERE a.id=slack_conversations.id AND a.retry_at>strftime('%s','now')) ORDER BY created LIMIT 1").fetchone()
   if not found:return False
   row=dict(found)
   if row['connection_id'] not in self.service.web:return False
   if row['state'] in ('received','control_ready'):self.prepare(row);return True
   self.set_state(row['id'],'planning')
  try:
   with self.store.connect() as c:
    active=slack_direct.task_in_thread(c,row['channel'],row['thread_ts'])
    previous=[dict(r) for r in c.execute("SELECT q.payload,p.content FROM slack_conversations q JOIN slack_conversation_posts p ON p.request_id=q.id AND p.kind='reply' AND p.state='sent' WHERE q.channel=? AND q.agent_id=? AND q.id<>? ORDER BY q.created DESC LIMIT 2",(row['channel'],row['agent_id'],row['id']))]
   if not row['decision'] and active and active['status'] in ('queued','running'):
    self.prepare({**row,'state':'received'});return True
   event=json.loads(row['payload'])['event'];text=re.sub(r'<@[A-Z0-9]+>','',event.get('text','')).strip();team=team_settings(self.store);chief=leader(team['agents'])
   payload={'conversation':True,'recent_conversation':[{'user':json.loads(r['payload'])['event'].get('text','')[:350],'answer':r['content'][:500]} for r in reversed(previous)],'title':text or '첨부 이미지를 확인해 주세요.','agents':team['agents'],'workspace':frozen_context(self.store),'replying_agent':next((a for a in team['agents'] if a['id']==row['agent_id']),chief),'current_work':self.status_text()}
   decision=json.loads(row['decision']) if row['decision'] else self.decider.decide(payload)
   with self.store.connect() as c:c.execute('UPDATE slack_conversations SET decision=? WHERE id=?',(json.dumps(decision,ensure_ascii=False),row['id']))
   if decision['action']=='delegate':
    context={'channel':row['channel'],'thread_ts':row['thread_ts'],'connection_id':row['connection_id'],'agent_id':'shared','user_id':event['user'],'pending_attachments':bool(event.get('files'))}
    tid=self.store.add(payload['title'],'llama',source_key='slack-chat:'+row['id'],environment='vm' if self.service.config.get('collaboration') else 'local',slack_context=context,leader_decision={**decision,'agents':team['agents'],'leader_id':chief['id'],'request_id':row['id'][:12]})
    if event.get('files'):
     try:self.attach(tid,row,event)
     except Exception:
      with self.store.connect() as c:c.execute("UPDATE tasks SET status='interrupted',wait_reason='slack_attachment',error='Slack 첨부 이미지를 받지 못했습니다.' WHERE id=?",(tid,))
      raise
     with self.store.connect() as c:c.execute('UPDATE tasks SET retry_at=0 WHERE id=?',(tid,))
    self.send(row,'reply','업무를 접수하고 담당자에게 전달했습니다.\n'+decision['summary']+'\n작업 ID: '+tid);self.set_state(row['id'],'delegated',task_id=tid)
   else:self.send(row,'reply',decision['answer']);self.set_state(row['id'],'replied')
  except Exception as e:
   message=str(e)[:300] if isinstance(e,ValueError) else type(e).__name__
   with self.store.connect() as c:
    c.execute('INSERT OR IGNORE INTO slack_conversation_attempts(id) VALUES(?)',(row['id'],));c.execute('UPDATE slack_conversation_attempts SET attempts=attempts+1,retry_at=? WHERE id=?',(time.time()+30,row['id']))
    attempts=c.execute('SELECT attempts FROM slack_conversation_attempts WHERE id=?',(row['id'],)).fetchone()[0]
   self.set_state(row['id'],'reply_pending' if attempts<3 else 'failed',error=message)
   try:self.send(row,'error','답변 준비 중 문제가 발생했습니다: '+message+'\n요청은 보존했으며 다시 확인하겠습니다.')
   except Exception:pass
  return True
 def loop(self):
  while not self.stop.is_set():
   try:self.step()
   except Exception:pass
   self.stop.wait(1)
