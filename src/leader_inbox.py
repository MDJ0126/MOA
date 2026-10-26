"""Durable leader inbox: answer directly or publish a validated delegated task."""
import json,re,threading,time,uuid,urllib.request
import routing_cache
from setup_config import team_settings,read_setting
from organization import display_name,instruction
from workspace_resources import frozen_context
from local_model import headers
from model_transport import request_json,completion,model_output

ROUTING_PROMPT='''You are the selected MOA teammate (default: root leader). Use polite Korean with name, rank and 님. Respect each person's duties, specialty, instructions, work kinds, boundaries and reporting lines. Internal developer/reviewer labels are execution/check slots, not job titles.
Ordinary conversation: reply directly. Real work: choose code/image/research/design/general. Research and image acquisition are not coding. A leader with subordinates is a manager: delegate execution to a suitable subordinate, inspect their evidence, then issue targeted correction or recheck instructions. Do not execute or verify your own delegated work. If no subordinate is eligible, explain the missing duty assignment instead of silently doing it yourself. A lone agent or explicitly addressed leaf teammate may execute directly. Never merely propose work or claim completion before execution. Code needs independent review where available. Delegate includes an execution owner (developer slot) and optional reviewer; application adds missing checks. Add a reporter only when explicitly requested: MOA already sends progress, screenshots and final results through Slack. Public summary says who does what.
There is no image-understanding model: source metadata is not visual proof. Check sources, integrity and access; disclose visual limits. Return schema JSON: action, work_kind, summary, answer, assignments, refinement. Work answers are empty; assignments contain concise role goals, never duplicate the entire original request.'''

def explicit_reporter_requested(payload):
    return bool(re.search(r'\breporter\b|보고\s*(?:전담|담당)|별도(?:의)?\s*보고|separate\s+report',payload.get('title',''),re.I))

def routing_context(payload):
    """Keep routing semantics and user instructions; omit duplicate/generated UI data."""
    result=dict(payload)
    keys=('id','name','rank','job_title','manager_id','capabilities','specialty',
          'work_kinds','responsibilities','boundaries','instructions','role_preset')
    result['agents']=[{k:a[k] for k in keys if k in a} for a in payload['agents']]
    workspace=dict(payload.get('workspace') or {})
    text=workspace.get('global_instructions','')
    # Only remove the exact application-generated suffix, never arbitrary user text.
    suffix='\n\n'+instruction(payload['agents'])
    if text.endswith(suffix):workspace['global_instructions']=text[:-len(suffix)]
    if workspace:result['workspace']=workspace
    return result

def leader(agents,recipient_id=None):
    if recipient_id:
        selected=next((a for a in agents if a["id"]==recipient_id),None)
        if not selected:raise ValueError("요청을 받을 팀원을 찾을 수 없습니다. 다시 선택해 주세요.")
        return selected
    return next((a for a in agents if not a.get('manager_id')),agents[0])

REFINEMENT_FIELDS={'goal':'목적','requirements':'요구사항','deliverables':'결과물','acceptance':'완료 조건','constraints':'제약 사항','unknowns':'미확인 사항','changes':'정리한 내용'}

def validate_refinement(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!=set(REFINEMENT_FIELDS):raise ValueError('리더 요청 정리 항목을 확인하지 못했습니다.')
    if any(not isinstance(v,str) or len(v)>1200 for v in value.values()) or not value['goal'].strip():raise ValueError('리더 요청 정리 내용이 비어 있거나 너무 깁니다.')
    return {k:v.strip() for k,v in value.items()}

def refinement_text(value):
    return '\n'.join(label+': '+(value.get(key) or '미확인 — 원문에 명시되지 않음') for key,label in REFINEMENT_FIELDS.items())

def reports_to(agents,chief):
    """Only the reporting subtree; flat legacy teams use the root as manager."""
    if not any(a.get('manager_id') for a in agents):
        return [a for a in agents if a['id']!=chief['id']] if leader(agents)['id']==chief['id'] else []
    selected=set();pending={chief['id']}
    while pending:
        children={a['id'] for a in agents if a.get('manager_id') in pending and a['id']!=chief['id']} - selected
        selected.update(children);pending=children
    return [a for a in agents if a['id'] in selected]

def can_execute(agent,kind):
    if agent.get('work_kinds'):return kind in agent['work_kinds'] and (kind!='code' or 'developer' in agent['capabilities'])
    if kind=='code':return 'developer' in agent['capabilities']
    tags={'image':('이미지','사진','리서치','조사','검색','콘텐츠','image','photo','research'), 'research':('조사','리서치','분석','자료','research','analysis'), 'design':('디자인','그래픽','design','ui','ux'), 'general':('운영','관리','지원','기획','문서','general','support')}
    expertise=' '.join(str(agent.get(k,'')) for k in ('job_title','specialty','responsibilities','instructions')).lower()
    return any(t in expertise for t in tags[kind])

def delegate_assignments(agents,chief,items,kind):
    children=reports_to(agents,chief)
    if not children:return items
    eligible=[a for a in children if can_execute(a,kind)]
    if not eligible:raise ValueError('리더가 배정할 수 있는 하위 담당자가 없습니다. 담당할 수 있는 역할을 확인하세요. 팀원의 담당 가능한 업무에 '+{'code':'코드·개발','image':'이미지 수집','research':'리서치','design':'디자인','general':'일반 업무'}[kind]+'를 지정하거나 해당 담당자를 추가해 주세요. 리더가 대신 실행하지 않았습니다.')
    owner=next((a for a in items if a.get('role')=='developer'),None)
    if owner is None:return items
    allowed={a['id'] for a in eligible}
    if kind=='code' and owner.get('agent_id')!=chief['id'] and owner.get('agent_id') not in allowed:raise ValueError('해당 실행 역할을 맡을 수 없는 팀원입니다.')
    if owner.get('agent_id') not in allowed:
        # Preserve suitable model choices; prefer leaving a checker available.
        specialist={'image':('research','content','graphic','ui'), 'research':('research','data','ux'), 'code':('fullstack','frontend','backend','automation','unity','unreal'), 'design':('ui','graphic','illustration','game-design'), 'general':('operations','product','reporter')}
        selected=max(eligible,key=lambda a:(int(a.get('role_preset') in specialist[kind]),int(any(t in (a.get('specialty','')+' '+a.get('job_title','')).lower() for t in {'image':('이미지','사진','리서치','조사','콘텐츠'), 'research':('리서치','조사','분석'), 'code':('개발',), 'design':('디자인','그래픽'), 'general':('운영','지원','기획')}[kind])),int('reviewer' not in a['capabilities'])))
        owner['agent_id']=selected['id']
    checker=next((a for a in items if a.get('role')=='reviewer'),None)
    if kind=='code' and checker and checker.get('agent_id')!=chief['id'] and (checker.get('agent_id')==owner['agent_id'] or not any(a['id']==checker.get('agent_id') and 'reviewer' in a['capabilities'] for a in agents)):raise ValueError('독립 검증이 가능한 다른 팀원을 선택하세요.')
    others=[a for a in children if a['id']!=owner['agent_id'] and (kind!='code' or 'reviewer' in a['capabilities'])]
    qualified=[a for a in others if 'reviewer' in a['capabilities']]
    if checker and qualified and checker.get('agent_id') not in {a['id'] for a in qualified}:checker['agent_id']=max(qualified,key=lambda a:int(a.get('role_preset')=='qa'))['id']
    if checker and others and checker.get('agent_id') not in {a['id'] for a in others}:
        checker['agent_id']=max(others,key=lambda a:(int('reviewer' in a['capabilities']),int(a.get('role_preset')=='qa')))['id']
    elif checker and not others:
        # One subordinate performs an explicit recheck; the leader still reviews separately.
        checker['agent_id']=owner['agent_id']
    return items

def validate(agents,value,options):
    if not isinstance(value,dict) or value.get('action') not in ('reply','delegate','execute'):raise ValueError('리더의 응답 또는 업무 배정 결정을 확인하지 못했습니다.')
    refinement=validate_refinement(value.get('refinement'));extra={'refinement':refinement} if refinement else {}
    summary=value.get('summary','')
    if not isinstance(summary,str) or not 1<=len(summary.strip())<=2000:raise ValueError('리더의 공개 판단 사유가 필요합니다.')
    if value['action']=='reply':
        if options.get('required_files') or options.get('acceptance') or options.get('vm_actions'):raise ValueError('결과물·검증 조건이 있는 요청은 담당자에게 배정해야 합니다.')
        answer=value.get('answer','')
        if not isinstance(answer,str) or not 1<=len(answer.strip())<=6000:raise ValueError('리더의 답변이 없습니다.')
        return {'action':'reply','summary':summary.strip(),'answer':answer.strip(),'assignments':[],**extra}
    kind=value.get('work_kind','code')
    from git_publication import decision as git_decision
    extra['git']=git_decision(value.get('git'),options,kind)
    if kind not in ('code','image','research','design','general'):raise ValueError('업무 종류를 확인하세요.')
    items=value.get('assignments');lookup={a['id']:a for a in agents};chief=leader(agents,options.get('recipient_id'))
    if value['action']=='execute':
        reviewer=next((a for a in agents if a['id']!=chief['id'] and 'reviewer' in a['capabilities']),chief) if kind=='code' else chief
        items=[{'role':'developer','agent_id':chief['id'],'instruction':(options.get('title') or summary)[:1800]},{'role':'reviewer','agent_id':reviewer['id'],'instruction':'실행 결과와 실제 근거를 확인하고 실패를 통과 처리하지 마세요.'}]
    if kind!='code' and isinstance(items,list) and len(items)==1 and isinstance(items[0],dict) and items[0].get('role')=='developer' and items[0].get('agent_id') in lookup:
        owner=items[0]['agent_id']
        checker=next((a['id'] for a in agents if a['id']!=owner and a.get('role_preset')=='qa' and 'reviewer' in a['capabilities']),owner)
        items=items+[{'role':'reviewer','agent_id':checker,'instruction':'출처와 산출물을 실제로 대조하고 필요한 실행 검증을 수행하세요. 보고서의 주장을 확보한 근거와 비교하고, 실패나 미확인을 통과로 보고하지 마세요.'}]
    if not isinstance(items,list) or not 2<=len(items)<=3:raise ValueError('구현·검증 담당과 선택적인 보고 담당이 필요합니다.')
    if all(isinstance(item,dict) for item in items):items=delegate_assignments(agents,chief,[dict(item) for item in items],kind)
    seen=set();clean=[]
    for item in items:
        if not isinstance(item,dict):raise ValueError('담당자 배정 형식 오류')
        role=item.get('role');aid=item.get('agent_id');text=item.get('instruction')
        if role not in ('developer','reviewer','reporter') or role in seen or aid not in lookup:raise ValueError('허용된 실행 역할과 팀원을 지정하세요.')
        if kind!='code' and role=='developer' and aid!=chief['id'] and not lookup[aid].get('work_kinds'):
            expertise=' '.join(str(lookup[aid].get(k,'')) for k in ('job_title','specialty','instructions')).lower()
            tags={'image':('이미지','사진','리서치','조사','검색','콘텐츠','image','photo','research'), 'research':('조사','리서치','분석','자료','research','analysis'), 'design':('디자인','그래픽','design','ui','ux'), 'general':('운영','관리','지원','기획','general','support')}
            if not any(tag in expertise for tag in tags[kind]):
                aid=next((a['id'] for a in agents if a['id']!=chief['id'] and any(tag in ' '.join(str(a.get(k,'')) for k in ('job_title','specialty','instructions')).lower() for tag in tags[kind])),chief['id'])
        if role=='developer' and lookup[aid].get('work_kinds') and kind not in lookup[aid]['work_kinds']:
            eligible=[a for a in agents if kind in a.get('work_kinds',[]) and (kind!='code' or 'developer' in a['capabilities'] or a['id']==chief['id'])]
            tags={'code':('개발','developer','programmer'), 'image':('이미지','사진','리서','조사','검색','콘텐츠','그래픽','일러스트','image','photo','research'), 'research':('조사','리서','분석','research','analysis'), 'design':('디자인','그래픽','일러스트','게임 기획','design'), 'general':('운영','관리','지원','기획','general','support')}
            match=next((a for a in eligible if a['id']!=chief['id'] and any(t in (a.get('job_title','')+' '+a.get('specialty','')).lower() for t in tags[kind])),None) or next((a for a in eligible if a['id']==chief['id']),None) or next(iter(eligible),None)
            if match is None:raise ValueError('팀에 이 업무를 담당할 수 있는 역할이 없습니다. 역할의 담당 가능한 업무 범위를 수정하거나 적합한 팀원을 추가하세요.')
            aid=match['id']
        allowed=kind!='code' or role in lookup[aid]['capabilities'] or aid==chief['id'] and (role=='reporter' or (value['action']=='execute' or value.get('self_execution') is True))
        if not allowed:raise ValueError('해당 실행 역할을 맡을 수 없는 팀원입니다.')
        if not isinstance(text,str) or not 1<=len(text.strip())<=2000:raise ValueError('담당자별 업무 지시가 필요합니다.')
        if kind in ('image','research') and '이미지의 육안 내용 판별은 지원되지 않습니다.' not in text:
            text=text[:1800]
            text+='\n이미지의 육안 내용 판별은 지원되지 않습니다. 출처·파일 형식·해시·접근 가능 여부를 검증하고 시각 판별을 완료했다고 말하지 마세요.'
        seen.add(role);clean.append({'role':role,'agent_id':aid,'instruction':text.strip()})
    if not {'developer','reviewer'}.issubset(seen):raise ValueError('실행 업무에는 구현 후 독립 검증이 필요합니다.')
    clean.sort(key=lambda a:('developer','reviewer','reporter').index(a['role']))
    dev=next(a['agent_id'] for a in clean if a['role']=='developer');review=next(a['agent_id'] for a in clean if a['role']=='reviewer')
    independent_requested=bool(re.search(r'\b(?:different|separate)\s+reviewer\b|다른\s*(?:검증자|검토자|담당자|팀원)|별도(?:의)?\s*검증\s*담당',options.get('title',''),re.I))
    if dev==review and independent_requested:
        other=next((a['id'] for a in agents if a['id']!=dev and 'reviewer' in a['capabilities']),None)
        if other is None:raise ValueError('요청한 독립 검증을 맡을 다른 팀원이 없습니다.')
        next(a for a in clean if a['role']=='reviewer')['agent_id']=other;review=other
    if kind=='code' and dev==review and any(a['id']!=dev and 'reviewer' in a['capabilities'] for a in agents):raise ValueError('독립 검증이 가능한 다른 팀원을 선택하세요.')
    if kind!='code':
        label={'image':'이미지 확보','research':'자료 조사','design':'디자인 작업','general':'업무 처리'}[kind]
        summary=(display_name(lookup[dev])+' · '+label+'와 필요한 근거 확인을 직접 수행합니다.') if dev==review else (display_name(lookup[dev])+'이 '+label+'를 맡고, '+display_name(lookup[review])+'이 결과 근거를 확인합니다.')
    if reports_to(agents,chief):summary=display_name(chief)+'이 지시하고, '+display_name(lookup[dev])+'이 실행합니다. '+(display_name(lookup[review])+'의 독립 검증 후 ' if dev!=review else '담당자 재검증 후 ')+ '리더가 결과를 재검토하고 필요한 수정·재확인을 지시합니다.'
    return {'action':'delegate','work_kind':kind,'self_execution':dev==chief['id'],'summary':summary.strip(),'answer':'','assignments':clean,**extra}

class LeaderInbox:
    def __init__(self,store,model,url):
        self.store=store;self.model=model;self.url=url;self.stop=threading.Event();self.thread=None;self.lock=threading.Lock()
        routing_cache.initialize(store)
        with store.connect() as c:c.execute("CREATE TABLE IF NOT EXISTS leader_requests(id TEXT PRIMARY KEY,request_key TEXT UNIQUE,payload TEXT,state TEXT,leader_id TEXT,leader_name TEXT,decision TEXT DEFAULT '',answer TEXT DEFAULT '',task_id TEXT DEFAULT '',error TEXT DEFAULT '',created REAL,updated REAL)")
    def recover(self):
        with self.store.connect() as c:c.execute("UPDATE leader_requests SET state='queued' WHERE state='planning'")
        self.start()
    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():return
            self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def get(self,key):
        with self.store.connect() as c:row=c.execute('SELECT id,state,leader_id,leader_name,answer,task_id,error,created,updated,decision,payload FROM leader_requests WHERE id=?',(key,)).fetchone()
        if not row:raise ValueError('리더 접수 기록을 찾을 수 없습니다.')
        result=dict(row);payload=json.loads(result.pop('payload'));result['title']=payload.get('title','');result['parent_request_id']=payload.get('parent_request_id','');result['decision']=json.loads(result['decision']) if result['decision'] else None
        result['refinement']=(result['decision'] or {}).get('refinement');editor=leader(payload['agents']);result['refinement_author']=display_name(editor)
        return {'id':key,'leader_request':True,**result}
    def recent(self):
        with self.store.connect() as c:
            rows=c.execute('SELECT id,state,leader_id,leader_name,answer,task_id,error,created,updated,payload,decision FROM leader_requests ORDER BY created DESC LIMIT 100').fetchall()
        result=[]
        for row in rows:
            item=dict(row);payload=json.loads(item.pop('payload'));decision=json.loads(item.pop('decision') or '{}');item['refinement']=decision.get('refinement');item['refinement_author']=display_name(leader(payload['agents']));result.append({**item,'title':payload.get('title',''),'parent_request_id':payload.get('parent_request_id',''),'leader_request':True})
        return result
    def submit(self,data):
        title=data.get('title');key=data.get('request_key')
        if not isinstance(title,str) or not 1<=len(title.strip())<=8000:raise ValueError('맡길 일을 8,000자 이내로 입력해 주세요.')
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,80}',key):raise ValueError('요청 ID 오류')
        team=team_settings(self.store)
        if team.get('recovery_required'):raise ValueError('팀 설정을 먼저 복구해 주세요.')
        payload={k:v for k,v in data.items() if k in ('title','mode','acceptance','max_revisions','auto_recover','required_files','environment','vm_actions','expected_profile_updated','recipient_id','parent_request_id','hold_publication')};payload.setdefault('environment','local' if data.get('mode')=='demo' else 'profile');payload['agents']=team['agents'];payload['workspace']=frozen_context(self.store)
        if payload.get('parent_request_id'):
            parent=self.get(payload['parent_request_id'])
            payload['recipient_id']=parent['leader_id']
            payload['recent_conversation']=[{'role':'user','content':parent['title']},{'role':'assistant','content':parent['answer']}]
        packed=json.dumps(payload,ensure_ascii=False,sort_keys=True);chief=leader(team['agents'],payload.get('recipient_id'))
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT id,payload FROM leader_requests WHERE request_key=?',(key,)).fetchone()
            if old:
                if {k:v for k,v in json.loads(old['payload']).items() if k not in ('agents','workspace')}!={k:v for k,v in payload.items() if k not in ('agents','workspace')}:raise ValueError('같은 요청 ID에 다른 내용이 전달되었습니다.')
                ident=old['id']
            else:
                ident=uuid.uuid4().hex[:12];now=time.time();c.execute('INSERT INTO leader_requests(id,request_key,payload,state,leader_id,leader_name,created,updated) VALUES(?,?,?,?,?,?,?,?)',(ident,key,packed,'queued',chief['id'],display_name(chief),now,now))
        self.start();return self.get(ident)
    def decide(self,payload):
        chief=leader(payload['agents'],payload.get('recipient_id'));prompt=ROUTING_PROMPT+'\nAssign each participant a concrete personal improvement objective using the supplied leader growth feedback and comparable prior evidence. Include it in assignment instruction without adding unrelated work. Ordinary task success is neutral. Ask for observable verification of the specific earlier weakness, never praise or points trading.'
        if payload.get('recipient_id'):prompt+='\nThe user explicitly addressed '+display_name(chief)+'. Reply as this person. If you manage subordinates, delegate real execution to them; if you are a leaf teammate, execute fitting work yourself.'
        if payload.get('conversation'):prompt+='\nThis is a live Slack conversation. Answer ordinary questions in 1-4 concise Korean sentences. Use recent_conversation to understand follow-ups. Answer the user yourself; never repeat their instructions as a request for them to answer. Research and file creation use actual tools: as a manager delegate to suitable subordinates; only a lone/leaf teammate executes directly. Never claim completion before execution.'
        if payload.get('replying_agent'):prompt+='\nReply as this actual conversation owner: '+display_name(payload['replying_agent'])+'. Use current_work as observed status, never claim unexecuted work completed.'
        prompt+='\nRoot leader '+display_name(leader(payload['agents']))+' first organizes the request publicly into refinement fields goal, requirements, deliverables, acceptance, constraints, unknowns, changes (Korean strings), then routes it in THIS single response. Preserve explicit filenames, counts, URLs, constraints and acceptance code. Original requirements take precedence. Do not invent scope or turn unknown optional details into blockers. For ordinary replies use only a short goal and leave other fields empty. This brief is public, never private reasoning.'
        prompt+='\nKeep refinement concise: one short clause per field, acceptance may quote exact checks. Avoid repeating the same shell commands across goal, requirements, changes and assignments. The full original request remains available to the executor and takes precedence.'
        prompt+='\nAlso decide git publication: publish verified code/projects or an explicitly requested repository deliverable; skip casual conversation, private/sensitive content and images/research without a repository request. Respect no-upload instructions. Supply git={publish:boolean,reason:short Korean public reason,slug:short meaningful English kebab-case project name}. It runs only after verification passes and repository auto-publish is enabled.'
        body={'model':self.model,'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps(routing_context(payload),ensure_ascii=False,separators=(',',':'))}],'temperature':.1,'max_tokens':1000 if payload.get('conversation') else 1500,'response_format':{'type':'json_schema','json_schema':{'name':'leader_route','strict':True,'schema':{'type':'object','properties':{'git':{'type':'object','properties':{'publish':{'type':'boolean'},'reason':{'type':'string','maxLength':180},'slug':{'type':'string','maxLength':60}},'required':['publish','reason','slug'],'additionalProperties':False},'refinement':{'type':'object','properties':{k:{'type':'string','maxLength':{'goal':120,'requirements':160,'deliverables':120,'acceptance':1200,'constraints':200,'unknowns':80,'changes':80}[k]} for k in REFINEMENT_FIELDS},'required':list(REFINEMENT_FIELDS),'additionalProperties':False},'action':{'type':'string','enum':['reply','delegate','execute']},'work_kind':{'type':'string','enum':['code','image','research','design','general']},'summary':{'type':'string','maxLength':160},'answer':{'type':'string','maxLength':1800},'assignments':{'type':'array','maxItems':3,'items':{'type':'object','properties':{'role':{'type':'string','enum':['developer','reviewer','reporter']},'agent_id':{'type':'string','enum':[a['id'] for a in payload['agents']]},'instruction':{'type':'string','maxLength':280}},'required':['role','agent_id','instruction'],'additionalProperties':False}}},'required':['action','work_kind','summary','answer','assignments','refinement','git'],'additionalProperties':False}}}}
        if payload.get('required_files') or payload.get('acceptance') or payload.get('vm_actions'):
            props=body['response_format']['json_schema']['schema']['properties']
            props['action']['enum']=['delegate','execute']
            props['answer']={'type':'string','enum':['']}
            # Routing is a short public brief, not another copy of the task.
            # The complete original and acceptance stay in routing_context and
            # the execution plan; these limits affect generated prose only.
            props['summary']['maxLength']=80
            props['assignments']['items']['properties']['instruction']['maxLength']=120
            props['git']['properties']['reason']['maxLength']=60
            for key,limit in {'goal':80,'requirements':120,'deliverables':80,'acceptance':300,'constraints':120,'unknowns':40,'changes':40}.items():
                props['refinement']['properties'][key]['maxLength']=limit
            body['messages'][0]['content']+='\nFor this concrete work request, minimize generated routing prose: brief clauses, no repeated code. Acceptance names ALL original checks rather than copying shell commands. The complete original task and explicit checks are preserved verbatim and always take precedence. Assignment instructions describe only each role goal. Leave unknowns and changes empty when there are none. Do not omit duties or alter the original execution requirements.'
        if not explicit_reporter_requested(payload):
            assignments=body['response_format']['json_schema']['schema']['properties']['assignments']
            assignments['maxItems']=2
            assignments['items']['properties']['role']['enum']=['developer','reviewer']
        children=reports_to(payload['agents'],chief)
        if children:
            props=body['response_format']['json_schema']['schema']['properties']
            props['action']['enum']=[action for action in props['action']['enum'] if action!='execute']
            props['assignments']['items']['properties']['agent_id']['enum']=[a['id'] for a in children]
        req=urllib.request.Request(self.url+'/v1/chat/completions',json.dumps(body).encode(),headers(self.url))
        raw,finish=completion(request_json(req,timeout=240))
        if finish=='length':raise ValueError('리더의 판단 응답이 잘렸습니다. 다시 요청해 주세요.')
        value=model_output(raw)
        if value.get('action')!='reply' and not value.get('refinement'):raise ValueError('리더가 정리한 요청이 없어 업무를 배정하지 않았습니다.')
        return validate(payload['agents'],value,payload)
    def step(self):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if read_setting(self.store,'paused',False) or c.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running')").fetchone():return False
            row=c.execute("SELECT * FROM leader_requests WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:return False
            job=dict(row);c.execute("UPDATE leader_requests SET state='planning',updated=? WHERE id=?",(time.time(),job['id']))
        try:
            payload=json.loads(job['payload']);cache_key=routing_cache.key(payload,self.model,self.url)
            enabled=read_setting(self.store,'routing_cache_enabled',True)
            cached=routing_cache.get(self.store,cache_key) if enabled and not job['decision'] else None
            if cached:
                try:cached_decision=validate(payload['agents'],cached[0],payload)
                except (ValueError,TypeError,KeyError):cached=None
            if job['decision']:decision=json.loads(job['decision'])
            elif cached:
                decision=cached_decision
                decision['routing_cache']={'hit':True,'origin_task':cached[1]}
            else:
                decision=self.decide(payload)
                decision['routing_cache']={'hit':False}
            if payload.get('hold_publication'):
                decision['git']={'publish':False,'reason':'지속 목표 결과 · 사용자 검토 전 자동 게시 보류','slug':decision.get('git',{}).get('slug','')}
            with self.store.connect() as c:c.execute('UPDATE leader_requests SET decision=? WHERE id=?',(json.dumps(decision,ensure_ascii=False),job['id']))
            if decision['action']=='delegate':
                args={k:v for k,v in payload.items() if k not in ('title','mode','agents','workspace','recipient_id','parent_request_id','recent_conversation','hold_publication')}
                tid=self.store.add(payload['title'],payload.get('mode','llama'),request_key='leader_'+job['id'],leader_decision={**decision,'agents':payload['agents'],'leader_id':job['leader_id'],'request_id':job['id']},**args)
                routing_cache.put(self.store,cache_key,decision,tid)
                answer=decision['summary'];state='delegated'
            else:tid='';answer=decision['answer'];state='replied'
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                for role,text,suffix in [('user',payload['title'],'user'),('assistant',answer,'answer')]:
                    c.execute("INSERT OR IGNORE INTO office_chat(id,agent_id,role,content,state,created,request_key) VALUES(?,?,?,?,?,?,?)",(uuid.uuid4().hex,job['leader_id'],role,text,'completed',time.time(),'leader_'+job['id']+'_'+suffix))
                c.execute('UPDATE leader_requests SET state=?,answer=?,task_id=?,updated=? WHERE id=?',(state,answer,tid,time.time(),job['id']))
        except Exception as error:
            message=str(error) if isinstance(error,ValueError) else '리더 접수 처리 실패 ('+type(error).__name__+')'
            with self.store.connect() as c:c.execute("UPDATE leader_requests SET state='failed',error=?,updated=? WHERE id=?",(message[:400],time.time(),job['id']))
        return True
    def loop(self):
        while not self.stop.is_set():
            try:
                if self.step():continue
            except Exception:pass
            self.stop.wait(1)
