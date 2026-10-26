"""Durable, bounded goal execution; finished work accumulates for human review."""
import ctypes,json,os,re,threading,time,uuid,urllib.request
from setup_config import read_setting
from local_model import headers
from model_transport import request_json, completion, model_output,ModelError


class Goals:
    def __init__(self,store,inbox,model,url):
        self.store,self.inbox,self.model,self.url=store,inbox,model,url
        self.stop=threading.Event();self.lock=threading.RLock();self.thread=None;self.current_goal=None;self.awake=False;self.power_error='';self.last_tick=0;self.last_error=''
        with store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS continuous_goals(
              id TEXT PRIMARY KEY,title TEXT NOT NULL,state TEXT NOT NULL,created REAL,updated REAL,
              deadline REAL,max_tasks INTEGER,reason TEXT DEFAULT '',failures INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS goal_items(
              id TEXT PRIMARY KEY,goal_id TEXT NOT NULL,ordinal INTEGER,title TEXT,request_id TEXT DEFAULT '',
              task_id TEXT DEFAULT '',state TEXT,review TEXT DEFAULT 'pending',feedback TEXT DEFAULT '',
              error TEXT DEFAULT '',created REAL,updated REAL,UNIQUE(goal_id,ordinal));''')
            if 'parent_item' not in {r[1] for r in c.execute('PRAGMA table_info(goal_items)')}:
                c.execute("ALTER TABLE goal_items ADD COLUMN parent_item TEXT DEFAULT ''")
            if 'request_key' not in {r[1] for r in c.execute('PRAGMA table_info(continuous_goals)')}:
                c.execute('ALTER TABLE continuous_goals ADD COLUMN request_key TEXT')
            if 'progress' not in {r[1] for r in c.execute('PRAGMA table_info(continuous_goals)')}:
                c.execute("ALTER TABLE continuous_goals ADD COLUMN progress TEXT DEFAULT ''")
            columns={r[1] for r in c.execute('PRAGMA table_info(continuous_goals)')}
            for name in ('retry_at','planner_failures'):
                if name not in columns:c.execute('ALTER TABLE continuous_goals ADD COLUMN '+name+' REAL DEFAULT 0')
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS goal_request_key ON continuous_goals(request_key)')
    def pending_review(self,c,gid):
        rows={r['id']:dict(r) for r in c.execute('SELECT * FROM goal_items WHERE goal_id=?',(gid,))}
        def approved(row,seen):
            if row['id'] in seen:return False
            if row['state']!='ready':return False
            if row['review']=='approved':return True
            if row['review']!='changes_requested':return False
            return any(approved(child,seen|{row['id']}) for child in rows.values() if child['parent_item']==row['id'])
        return not rows or any(not approved(row,set()) for row in rows.values())
    def snapshot(self):
        with self.store.connect() as c:
            goals=[dict(r) for r in c.execute('SELECT * FROM continuous_goals ORDER BY created DESC LIMIT 100')]
            for g in goals:
                g['items']=[dict(r) for r in c.execute('SELECT * FROM goal_items WHERE goal_id=? ORDER BY ordinal',(g['id'],))]
                for item in g['items']:
                    task=c.execute('SELECT status,error,wait_reason,verification,created FROM tasks WHERE id=?',(item['task_id'],)).fetchone()
                    item['task']=dict(task) if task else None
                    if task:
                        import task_timing,leader_review
                        timed=dict(task)|{'id':item['task_id']}
                        task_timing.enrich(c,[timed]);item['task']['timing']=timed.get('timing')
                        item['task']['quality_review']=leader_review.summary(c,item['task_id'])
        return {'goals':goals,'paused':read_setting(self.store,'paused',False),'keep_awake':self.awake,'power_error':self.power_error,'scheduler':{'alive':bool(self.thread and self.thread.is_alive()),'last_loop_at':self.last_tick,'error':self.last_error},'note':'모아와 PC가 켜져 있는 동안 실행합니다. 완료 결과는 검토함에 보관되며 자동 Git 게시를 보류합니다.'}
    def keep_awake(self,enabled):
        if os.name!='nt':return
        api=ctypes.WinDLL('kernel32',use_last_error=True).SetThreadExecutionState
        api.argtypes=[ctypes.c_uint];api.restype=ctypes.c_uint
        if not api(0x80000000|(1 if enabled else 0)):
            self.power_error='자동 절전 방지 요청 실패 · Windows 전원 설정을 확인하세요.';self.awake=False
        else:self.awake=bool(enabled);self.power_error=''
    def power_needed(self):
        if read_setting(self.store,'paused',False):return False
        with self.store.connect() as c:
            return bool(c.execute("SELECT 1 FROM continuous_goals WHERE state='active' AND deadline>? LIMIT 1",(time.time(),)).fetchone() or c.execute("SELECT 1 FROM goal_items i LEFT JOIN tasks t ON t.id=i.task_id LEFT JOIN leader_requests r ON r.id=i.request_id WHERE t.status IN ('queued','running') OR r.state IN ('queued','planning') LIMIT 1").fetchone())
    def create(self,data):
        title=data.get('title','').strip() if isinstance(data.get('title'),str) else ''
        hours=data.get('hours',24);limit=data.get('max_tasks',20)
        if not 1<=len(title)<=6000:raise ValueError('지속 목표를 1~6000자로 입력하세요.')
        if type(hours) not in (int,float) or not 1<=hours<=168:raise ValueError('실행 시간은 1~168시간입니다.')
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('작업 한도는 1~100개입니다.')
        key=data.get('request_key') or uuid.uuid4().hex
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,80}',key):raise ValueError('목표 요청 ID를 확인하세요.')
        now=time.time();ident=uuid.uuid4().hex[:12]
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM continuous_goals WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['title']!=title or old['max_tasks']!=limit or abs(old['deadline']-old['created']-hours*3600)>.01:raise ValueError('같은 목표 요청 ID에 다른 내용이 전달되었습니다.')
                return {'id':old['id']}
            c.execute('INSERT INTO continuous_goals(id,title,state,created,updated,deadline,max_tasks,request_key) VALUES(?,?,?,?,?,?,?,?)',(ident,title,'active',now,now,now+hours*3600,limit,key))
        return {'id':ident}
    def control(self,data):
        ident=data.get('id');action=data.get('action')
        with self.lock,self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');g=c.execute('SELECT * FROM continuous_goals WHERE id=?',(ident,)).fetchone()
            if not g:raise ValueError('지속 목표를 찾을 수 없습니다.')
            if action=='pause':state='paused'
            elif action=='stop':state='stopped'
            elif action=='resume':
                if g['deadline']<=time.time():raise ValueError('실행 시간이 끝났습니다. 새 목표를 만들어 주세요.')
                count=c.execute('SELECT COUNT(*) FROM goal_items WHERE goal_id=?',(ident,)).fetchone()[0]
                queued=c.execute("SELECT 1 FROM goal_items WHERE goal_id=? AND state='queued'",(ident,)).fetchone()
                if count>=g['max_tasks'] and not queued:raise ValueError('작업 한도에 도달했습니다. 결과를 검토하거나 새 목표를 만들어 주세요.')
                state='active'
            else:raise ValueError('목표 작업을 확인하세요.')
            c.execute("UPDATE continuous_goals SET state=?,reason='',failures=0,planner_failures=0,retry_at=0,updated=? WHERE id=?",(state,time.time(),ident))
        return {'state':state}
    def review(self,data):
        verdict=data.get('verdict');feedback=data.get('feedback','')
        if verdict not in ('approved','changes_requested') or not isinstance(feedback,str) or len(feedback)>2000:raise ValueError('검토 내용을 확인하세요.')
        if verdict=='changes_requested' and not feedback.strip():raise ValueError('수정할 내용을 입력해 주세요.')
        with self.lock,self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM goal_items WHERE id=?',(data.get('id'),)).fetchone()
            if not row or row['state']!='ready':raise ValueError('완료된 결과만 검토할 수 있습니다.')
            if row['review']!='pending':raise ValueError('이미 검토한 결과입니다.')
            task=c.execute('SELECT status,verification FROM tasks WHERE id=?',(row['task_id'],)).fetchone()
            if not task or task['status']!='completed' or json.loads(task['verification'] or '{}').get('passed') is not True:
                raise ValueError('실제 완료·검증 통과 상태를 확인하지 못했습니다. 결과와 실행 근거를 확인하세요.')
            c.execute('UPDATE goal_items SET review=?,feedback=?,updated=? WHERE id=?',(verdict,feedback,time.time(),row['id']))
            # Review is an acknowledgement, never an implicit merge/deploy/push.
            if verdict=='changes_requested':
                g=c.execute('SELECT * FROM continuous_goals WHERE id=?',(row['goal_id'],)).fetchone()
                count=c.execute('SELECT COUNT(*) FROM goal_items WHERE goal_id=?',(row['goal_id'],)).fetchone()[0]
                if count<g['max_tasks'] and time.time()<g['deadline'] and g['state']!='stopped':
                    self._insert(c,g,'이전 결과를 부분 수정하세요.\n사용자 검토: '+feedback+'\n이전 작업 ID: '+row['task_id']+'\n원래 업무: '+row['title'][:2000],row['id'])
                    c.execute("UPDATE continuous_goals SET state=?,reason='',updated=? WHERE id=?",('paused' if g['state']=='paused' else 'active',time.time(),g['id']))
                else:
                    c.execute("UPDATE continuous_goals SET state=?,reason='수정 요청 보관 · 목표 종료 또는 실행 시간·작업 한도 종료',updated=? WHERE id=?",('stopped' if g['state']=='stopped' else 'needs_review',time.time(),g['id']))
            elif c.execute("SELECT state FROM continuous_goals WHERE id=?",(row['goal_id'],)).fetchone()[0]=='needs_review':
                if not self.pending_review(c,row['goal_id']):c.execute("UPDATE continuous_goals SET state='completed',updated=? WHERE id=?",(time.time(),row['goal_id']))
        return {'review':verdict}
    def _insert(self,c,g,title,parent=''):
        ordinal=c.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM goal_items WHERE goal_id=?',(g['id'],)).fetchone()[0]
        now=time.time();ident=uuid.uuid4().hex[:12]
        c.execute('INSERT INTO goal_items(id,goal_id,ordinal,title,state,created,updated,parent_item) VALUES(?,?,?,?,?,?,?,?)',(ident,g['id'],ordinal,title[:7000],'queued',now,now,parent))
        return ident
    def propose(self,g,items):
        # Completed task status is evidence, not permission to invent completion.
        history=[{'task_id':r['task_id'],'title':r['title'][:120],'state':r['state'],'review':r['review'],'feedback':r['feedback'][:120],'error':r['error'][:120]} for r in items[-6:]]
        with self.store.connect() as c:
            for row in history:
                if row['task_id']:
                    task=c.execute('SELECT verification FROM tasks WHERE id=?',(row['task_id'],)).fetchone()
                    proof=json.loads(task['verification'] or '{}') if task else {}
                    row['verification']={k:proof.get(k) for k in ('passed','tests_run')}
                    if row is history[-1]:row['result']=[dict(r) for r in c.execute("SELECT kind,substr(content,1,250) AS content FROM messages WHERE task_id=? AND kind IN ('result','report','verification') ORDER BY seq DESC LIMIT 1",(row['task_id'],))]
        schema={'type':'object','properties':{'done':{'type':'boolean'},'reason':{'type':'string','maxLength':300},'task':{'type':'string','maxLength':2200},'progress':{'type':'string','maxLength':600}},'required':['done','reason','task','progress'],'additionalProperties':False}
        body={'model':self.model,'temperature':.1,'max_tokens':1100,'messages':[
            {'role':'system','content':'You are the MOA goal leader. Propose ONE concrete, useful, independently verifiable next task toward the user goal. Delegate by actual specialty through the existing team. Respect original scope, never invent new features or busywork. Carry every applicable explicit requirement into the task: output language, filenames, counts, format, restrictions and acceptance conditions. Never replace a real deliverable with generic English placeholder/sample text. Use completed task evidence; do not repeat completed or failed tasks. If the goal is satisfied, return done=true with evidence IDs and empty task. If blocked on authentication/license/user input, return done=true explaining the blocker, never fabricate results. Task must specify deliverable and objective acceptance checks. Output polite Korean JSON. Never publish, merge, deploy, pay, or accept terms; all outputs remain for morning human review.'},
            {'role':'user','content':json.dumps({'goal':g['title'],'prior_leader_note_unverified':g.get('progress',''),'actual_recent_history':history,'total_items':len(items),'completed_items':sum(r['state']=='ready' for r in items),'instruction':'Update progress with a concise public ledger of completed deliverables and IDs, outstanding requirements and blockers. The prior leader note is a planning aid, not execution proof. Never invent completed work.'},ensure_ascii=False)}],
            'response_format':{'type':'json_schema','json_schema':{'name':'goal_next','strict':True,'schema':schema}}}
        req=urllib.request.Request(self.url+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**headers(self.url)})
        raw,finish=completion(request_json(req,timeout=180))
        if finish=='length':raise ValueError('목표 계획 응답이 잘렸습니다.')
        result=model_output(raw)
        if type(result.get('done')) is not bool or not isinstance(result.get('reason'),str) or not isinstance(result.get('task'),str):raise ValueError('목표 계획 형식을 확인하지 못했습니다.')
        if not result['reason'].strip() or (not result['done'] and not result['task'].strip()):raise ValueError('목표 계획의 근거 또는 다음 업무가 없습니다.')
        return result
    def reconcile(self):
        with self.store.connect() as c:
            for row in c.execute("SELECT * FROM goal_items WHERE state IN ('submitted','failed')").fetchall():
                req=c.execute('SELECT state,task_id,error,answer FROM leader_requests WHERE id=?',(row['request_id'],)).fetchone()
                if not req:continue
                task=c.execute('SELECT status,error,wait_reason FROM tasks WHERE id=?',(req['task_id'],)).fetchone() if req['task_id'] else None
                failed=bool(task and (task['status'] in ('failed','cancelled') or task['status']=='interrupted' and task['wait_reason']!='resource_setup'))
                state='ready' if task and task['status']=='completed' else 'failed' if failed or req['state'] in ('failed','replied') else 'submitted'
                error=(task['error'] if task else req['error'] or ('실행 결과 없이 대화로 종료되었습니다.' if req['state']=='replied' else ''))[:1000]
                c.execute('UPDATE goal_items SET task_id=?,state=?,error=?,updated=? WHERE id=?',(req['task_id'],state,error,time.time(),row['id']))
                if state=='failed' and row['state']!='failed':c.execute('UPDATE continuous_goals SET failures=failures+1 WHERE id=?',(row['goal_id'],))
    def step(self):
        self.current_goal=None
        with self.lock:
            self.reconcile()
            if read_setting(self.store,'paused',False):return False
            with self.store.connect() as c:
                if c.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running')").fetchone() or c.execute("SELECT 1 FROM leader_requests WHERE state IN ('queued','planning')").fetchone():return False
                g=c.execute("SELECT * FROM continuous_goals WHERE state='active' AND retry_at<=? ORDER BY updated LIMIT 1",(time.time(),)).fetchone()
                if not g:return False
                g=dict(g);self.current_goal=g['id'];items=[dict(r) for r in c.execute('SELECT * FROM goal_items WHERE goal_id=? ORDER BY ordinal',(g['id'],))]
                if any(r['state']=='submitted' for r in items):return False
                reason='실행 시간 종료' if time.time()>=g['deadline'] else '실패 3회 · 확인 필요' if g['failures']>=3 else '작업 한도 도달' if len(items)>=g['max_tasks'] and not any(r['state']=='queued' for r in items) else ''
                if reason:
                    c.execute("UPDATE continuous_goals SET state=?,reason=?,updated=? WHERE id=?",('needs_review' if self.pending_review(c,g['id']) else 'completed',reason,time.time(),g['id']));return True
            import environment_profiles
            from vm_runtime import VM,settings as vm_settings
            profile=environment_profiles.settings(self.store)
            try:
                environment_profiles.resolve(profile,vm=vm_settings(self.store))
                if profile['location']=='shared_vm' and profile['target_os']=='windows':
                    session=VM(self.store).session();probe=read_setting(self.store,'windows_guest_probe',{})
                    if not session or session.get('config',{}).get('installation_only') or probe.get('session')!=session.get('name'):
                        raise ValueError('Windows 설치와 업무용 VM 명령 연결 확인 후 목표를 재개해 주세요.')
            except ValueError as e:
                with self.store.connect() as c:c.execute("UPDATE continuous_goals SET state='paused',reason=?,updated=? WHERE id=?",(str(e)[:500],time.time(),g['id']))
                return True
            item=next((r for r in items if r['state']=='queued'),None)
        # Release API lock while the model plans, then recheck goal/pause state.
        proposal=None if item else self.propose(g,items)
        with self.lock,self.store.connect() as c:
            current=c.execute('SELECT * FROM continuous_goals WHERE id=?',(g['id'],)).fetchone()
            if current['state']!='active' or read_setting(self.store,'paused',False):return False
            if current['deadline']<=time.time():
                c.execute("UPDATE continuous_goals SET state='needs_review',reason='실행 시간 종료',updated=? WHERE id=?",(time.time(),g['id']));return True
            if proposal:
                c.execute("UPDATE continuous_goals SET planner_failures=0,retry_at=0,reason='' WHERE id=?",(g['id'],))
                if isinstance(proposal.get('progress'),str):c.execute('UPDATE continuous_goals SET progress=? WHERE id=?',(proposal['progress'][:600],g['id']))
                if proposal['done']:
                    c.execute("UPDATE continuous_goals SET state=?,reason=?,updated=? WHERE id=?",('needs_review' if self.pending_review(c,g['id']) else 'completed','리더 판단 · '+proposal['reason'][:500],time.time(),g['id']));return True
                if any(proposal['task'].strip()==r['title'].strip() for r in items):raise ValueError('동일 업무 반복을 차단했습니다.')
                ident=self._insert(c,g,proposal['task']);item=dict(c.execute('SELECT * FROM goal_items WHERE id=?',(ident,)).fetchone())
        # Stable request key makes crash recovery safe even across submit/record gap.
        with self.lock:
            with self.store.connect() as c:
                if c.execute('SELECT state FROM continuous_goals WHERE id=?',(g['id'],)).fetchone()[0]!='active':return False
            req=self.inbox.submit({'title':item['title'][:3500]+'\n\n[배경 목표 — 이번에는 위 업무 한 항목만 수행하고 다른 항목은 이후 별도 배정합니다]\n'+g['title'][:3500]+'\n검증 결과와 파일을 보관하세요. Git 자동 게시·배포·병합은 하지 마세요.',
                'request_key':'goal_'+item['id'],'environment':'profile','mode':'llama','hold_publication':True})
            with self.store.connect() as c:
                c.execute("UPDATE goal_items SET state='submitted',request_id=?,updated=? WHERE id=?",(req['id'],time.time(),item['id']))
                c.execute('UPDATE continuous_goals SET updated=? WHERE id=?',(time.time(),g['id']))
        return True
    def record_error(self,error):
        with self.store.connect() as c:
            row=c.execute("SELECT planner_failures FROM continuous_goals WHERE id=? AND state='active'",(self.current_goal,)).fetchone()
            if not row:return
            failures=int(row[0])+1;retry=isinstance(error,ModelError) and error.retryable and failures<3
            delay=min(300,30*2**(failures-1)) if retry else 0
            if retry and error.retry_after is not None:delay=min(300,max(delay,error.retry_after))
            reason=(f'일시 오류 · {delay}초 뒤 재시도 ({failures}/3) · ' if retry else '자동 진행 중단 · ')+str(error)[:350]
            c.execute('UPDATE continuous_goals SET state=?,reason=?,planner_failures=?,retry_at=?,updated=? WHERE id=?',('active' if retry else 'paused',reason,failures,time.time()+delay if retry else 0,time.time(),self.current_goal))
    def loop(self):
        try:
            while not self.stop.is_set():
                self.last_tick=time.time()
                try:
                    self.keep_awake(self.power_needed())
                    self.step();self.last_error=''
                except Exception as e:
                    self.last_error=str(e)[:400]
                    try:self.record_error(e)
                    except Exception as storage_error:self.last_error='목표 상태 기록 실패 · '+str(storage_error)[:300]
                self.stop.wait(3)
        finally:self.keep_awake(False)
    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
