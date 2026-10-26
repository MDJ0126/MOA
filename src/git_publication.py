"""Readable publication layout and durable, post-verification Git jobs."""
import json,re,time
from pathlib import PurePosixPath
from setup_config import read_setting,write_setting


def decision(value,options,kind='code'):
    value=value if isinstance(value,dict) else {}
    denied=bool(re.search(r'(?:깃|git|github).{0,15}(?:올리지|업로드하지|저장하지|금지)|do not (?:push|publish|upload)',options.get('title',''),re.I))
    enabled=value.get('publish',kind=='code') is True and not denied
    slug=value.get('slug','');slug=slug if isinstance(slug,str) and re.fullmatch('[a-z][a-z0-9]*(?:-[a-z0-9]+)*',slug) and len(slug)<=60 else ''
    return {'publish':enabled,'reason':str(value.get('reason') or ('검증된 프로젝트 결과를 보관합니다.' if enabled else 'Git 보관 대상이 아닌 업무입니다.'))[:240],'slug':slug}


def layout(store,task,plan):
    import project_git as pg
    saved=read_setting(store,'project_git_layouts',{})
    if task['id'] in saved:return saved[task['id']]
    workflow=plan.get('leader_workflow',{});policy=workflow.get('git',{})
    title=(workflow.get('refinement',{}).get('goal') or task['title']).splitlines()[0]
    title=re.sub(r'^\[[^]]*\]\s*','',title).strip()[:90] or '작업 결과'
    required=json.loads(task.get('required_files') or '[]');stem=PurePosixPath(required[0]).stem if required else ''
    slug=policy.get('slug') or re.sub('[^a-z0-9]+','-',stem.lower()).strip('-') or pg.romanized_name(title)[:50] or 'project-results'
    slug=slug[:60].strip('-');kind=workflow.get('work_kind',plan.get('work_kind','code'))
    category={'code':'projects','research':'research','image':'assets','design':'design','general':'documents'}.get(kind,'projects')
    used={v['branch'] for v in saved.values()};branch='work/'+slug;suffix=2
    while branch in used:branch='work/'+slug+'-'+str(suffix);suffix+=1
    leaf=branch.split('/')[-1]
    return {'title':leaf.replace('-',' ').title() if policy.get('slug') else title,'description':title,'slug':leaf,'branch':branch,'path':category+'/'+leaf,'layout_version':2}


def initialize(store):
    with store.connect() as c:c.execute("CREATE TABLE IF NOT EXISTS git_publication_jobs(task_id TEXT PRIMARY KEY,state TEXT,reason TEXT,repository TEXT,attempts INTEGER DEFAULT 0,available REAL DEFAULT 0,error TEXT DEFAULT '',receipt TEXT DEFAULT '{}',created REAL,updated REAL)")


def enqueue(store,tid,c):
    import project_git as pg
    row=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    if not row or row['status']!='completed' or row['mode']=='demo':return False
    task=dict(row);plan=json.loads(task['execution_plan']);workflow=plan.get('leader_workflow',{})
    policy=workflow.get('git') or decision(None,task,workflow.get('work_kind',plan.get('work_kind','code')))
    settings=pg.settings(store)
    verified=json.loads(task.get('verification') or '{}').get('passed') is True
    allowed=policy['publish'] and settings['auto_publish'] and settings['connected'] and verified
    state='pending' if allowed else 'skipped';reason=policy['reason'] if allowed else ('검증 통과·저장소 연결·자동 업로드 설정을 확인하세요.' if policy['publish'] else policy['reason'])
    now=time.time();c.execute('INSERT OR IGNORE INTO git_publication_jobs(task_id,state,reason,repository,created,updated) VALUES(?,?,?,?,?,?)',(tid,state,reason,settings['url'],now,now))
    return allowed


def recover(store):
    with store.connect() as c:c.execute("UPDATE git_publication_jobs SET state='pending',updated=? WHERE state='running'",(time.time(),))


def recent(store):
    with store.connect() as c:
        return {r['task_id']:{**dict(r),'receipt':json.loads(r['receipt'])} for r in c.execute('SELECT * FROM git_publication_jobs ORDER BY created DESC LIMIT 600')}


def process_one(store):
    import project_git as pg
    settings=pg.settings(store)
    if not settings['auto_publish']:return False
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM git_publication_jobs WHERE state='pending' AND available<=? ORDER BY created LIMIT 1",(time.time(),)).fetchone()
        if not row:return False
        job=dict(row);c.execute("UPDATE git_publication_jobs SET state='running',attempts=attempts+1,updated=? WHERE task_id=?",(time.time(),job['task_id']))
    tid=job['task_id']
    try:
        if settings['url']!=job['repository']:raise ValueError('업무 완료 후 연결 저장소가 바뀌어 자동 업로드를 보류했습니다.')
        with store.connect() as c:task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        if task['status']!='completed' or json.loads(task.get('verification') or '{}').get('passed') is not True:raise ValueError('현재 작업이 검증 완료 상태가 아닙니다.')
        plan=json.loads(task['execution_plan']);aid=plan['assignment']['developer']
        store.log(tid,plan.get('leader_workflow',{}).get('leader_id',aid),'progress','Git 자동 업로드: '+job['reason'])
        receipt=pg.publish(store,tid,aid)
        with store.connect() as c:c.execute("UPDATE git_publication_jobs SET state='completed',error='',receipt=?,updated=? WHERE task_id=?",(json.dumps(receipt,ensure_ascii=False),time.time(),tid))
        store.log(tid,aid,'progress','Git 업로드 완료 · '+receipt['title']+' · '+receipt['url'])
    except Exception as error:
        message=str(error) if isinstance(error,ValueError) else 'Git 자동 업로드 실패 ('+type(error).__name__+')'
        attempts=job['attempts']+1;state='failed' if attempts>=3 else 'pending'
        with store.connect() as c:c.execute('UPDATE git_publication_jobs SET state=?,error=?,available=?,updated=? WHERE task_id=?',(state,message,time.time()+30*attempts,time.time(),tid))
        store.log(tid,'system','progress',message+(' · 자동 재시도 예정' if state=='pending' else ' · 작업 상세에서 수동 재시도할 수 있습니다.'))
    return True
