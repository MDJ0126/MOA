"""Local, per-teammate conversations. They never use Slack transport."""
import json
import threading
import time
import uuid
import urllib.request
from setup_config import team_settings
from workspace_resources import frozen_context
from local_model import headers
from model_transport import request_json,completion

class OfficeChat:
    def __init__(self,store,model,url,demo=False):
        self.store=store;self.model=model;self.url=url;self.demo=demo;self.lock=threading.Lock();self.worker=None
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS office_chat(id TEXT PRIMARY KEY,agent_id TEXT,role TEXT,content TEXT,state TEXT,created REAL,request_key TEXT UNIQUE,reply_to TEXT,error TEXT)')

    def recover(self):
        with self.store.connect() as c:c.execute("UPDATE office_chat SET state='interrupted',error='이전 대화 생성이 중단되었습니다. 다시 보내 주세요.' WHERE role='assistant' AND state IN ('queued','running')")

    def agent(self,aid):
        found=next((a for a in team_settings(self.store)['agents'] if a['id']==aid),None)
        if not found:raise ValueError('팀원을 찾을 수 없습니다.')
        return found

    def messages(self,aid):
        self.agent(aid)
        with self.store.connect() as c:rows=[dict(r) for r in c.execute('SELECT id,agent_id,role,content,state,created,reply_to,error FROM office_chat WHERE agent_id=? ORDER BY created DESC,rowid DESC LIMIT 100',(aid,))]
        from peer_scores import detail
        return {'scores':detail(self.store,aid),'agent_id':aid,'messages':list(reversed(rows)),'transport':'office','scope':'모아 안의 대화 · Slack으로 전송하지 않습니다.'}

    def send(self,value):
        aid=value.get('agent_id');self.agent(aid);text=value.get('content');key=value.get('request_key')
        import re
        if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise ValueError('대화는 1~4,000자로 입력하세요.')
        if not isinstance(key,str) or not re.fullmatch('[a-zA-Z0-9_-]{8,80}',key):raise ValueError('대화 요청 ID 오류')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT agent_id,content FROM office_chat WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['agent_id']!=aid or old['content']!=text.strip():raise ValueError('같은 대화 요청 ID에 다른 내용이 있습니다.')
            else:
                if c.execute("SELECT 1 FROM office_chat WHERE agent_id=? AND role='assistant' AND state IN ('queued','running')",(aid,)).fetchone():raise ValueError('이 팀원의 답변을 기다려 주세요.')
                now=time.time();mid=uuid.uuid4().hex
                c.execute('INSERT INTO office_chat VALUES(?,?,?,?,?,?,?,?,?)',(mid,aid,'user',text.strip(),'completed',now,key,'',''))
                c.execute('INSERT INTO office_chat VALUES(?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,aid,'assistant','','queued',now+.000001,None,mid,''))
        self.start();return self.messages(aid)

    def delegate(self,value):
        agent=self.agent(value.get('agent_id'));text=value.get('content');key=value.get('request_key')
        import re
        if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise ValueError('맡길 일을 1~4,000자로 입력하세요.')
        if not isinstance(key,str) or not re.fullmatch('[a-zA-Z0-9_-]{8,60}',key):raise ValueError('작업 요청 ID 오류')
        tid=self.store.add(text.strip(),'demo' if self.demo else 'llama',environment='local' if self.demo else 'guest',request_key='office_'+key,requested_agent=agent['id'])
        with self.store.connect() as c:
            now=time.time()
            c.execute('INSERT OR IGNORE INTO office_chat VALUES(?,?,?,?,?,?,?,?,?)',('task-user-'+tid,agent['id'],'user',text.strip(),'completed',now,None,'',''))
            c.execute('INSERT OR IGNORE INTO office_chat VALUES(?,?,?,?,?,?,?,?,?)',('task-receipt-'+tid,agent['id'],'assistant','업무로 접수했습니다. 작업 ID: '+tid,'completed',now+.000001,None,'task:'+tid,''))
        return {'task_id':tid,**self.messages(agent['id'])}

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():return
            self.worker=threading.Thread(target=self.loop,daemon=True);self.worker.start()

    def loop(self):
        while True:
            with self.lock:
                with self.store.connect() as c:
                    c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT * FROM office_chat WHERE role='assistant' AND state='queued' ORDER BY created LIMIT 1").fetchone()
                    if not row:
                        self.worker=None
                        return
                    item=dict(row);c.execute("UPDATE office_chat SET state='running' WHERE id=?",(item['id'],))
            try:
                agent=self.agent(item['agent_id']);context=frozen_context(self.store)
                context['skills']=[s for s in context['skills'] if not s.get('agents') or agent['id'] in s['agents']]
                system='You are '+agent['name']+', a named MOA teammate speaking to the user inside a small office. Respond in polite Korean, naturally and concisely. This is a direct conversation, separate from Slack. You have NO tools in this conversation. Never claim to have started a task, installed software, contacted Slack, inspected images or executed commands. For actual work, ask the user to use the 업무로 맡기기 button. Share concise explanations and observable status, not private reasoning. Follow the user workspace instructions and your assigned preferences.\n'+json.dumps({'agent':agent,'workspace':context},ensure_ascii=False)
                with self.store.connect() as c:history=[dict(r) for r in c.execute("SELECT role,content FROM office_chat WHERE agent_id=? AND state='completed' AND created<? ORDER BY created DESC,rowid DESC LIMIT 12",(agent['id'],item['created']))]
                if self.demo:text='데모 대화입니다. 실제 AI 응답은 실제 모드에서 확인하실 수 있습니다.'
                else:
                    payload={'model':self.model,'messages':[{'role':'system','content':system}]+list(reversed(history)),'temperature':.3,'max_tokens':600}
                    req=urllib.request.Request(self.url+'/v1/chat/completions',json.dumps(payload).encode(),headers(self.url))
                    text,finish=completion(request_json(req,timeout=240))
                    if finish=='length':text+='\n(응답 길이 한도에 도달했습니다.)'
                with self.store.connect() as c:c.execute("UPDATE office_chat SET state='completed',content=?,error='' WHERE id=?",(text[:12000],item['id']))
            except Exception as error:
                message=getattr(error,'message',None) or ('답변을 생성하지 못했습니다. 잠시 후 다시 보내 주세요. ('+type(error).__name__+')')
                with self.store.connect() as c:c.execute("UPDATE office_chat SET state='failed',error=? WHERE id=?",(str(message)[:500],item['id']))
