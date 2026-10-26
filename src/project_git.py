"""Managed project checkout and explicit, attributable result publication."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from urllib.parse import urlsplit
from setup_config import read_setting,write_setting,team_settings

LOCK=threading.Lock()

def legacy_author_email(agent):
    name=re.sub(r'[^\w.+-]','',unicodedata.normalize('NFC',agent['name']))[:80]
    return (name or agent['id'])+'@moa.local'


def romanized_name(value):
    initials=('g','kk','n','d','tt','r','m','b','pp','s','ss','','j','jj','ch','k','t','p','h')
    vowels=('a','ae','ya','yae','eo','e','yeo','ye','o','wa','wae','oe','yo','u','wo','we','wi','yu','eu','ui','i')
    finals=('','k','k','k','n','n','n','t','l','k','m','l','l','l','p','l','m','p','p','t','t','ng','t','t','k','t','p','t')
    # Common surname spellings; remaining Hangul syllables use deterministic transliteration.
    surnames={'김':'kim','이':'lee','박':'park','최':'choi','정':'jung','강':'kang','조':'cho','윤':'yoon','장':'jang','임':'lim','한':'han','오':'oh','서':'seo','신':'shin','권':'kwon','황':'hwang','안':'ahn','송':'song','홍':'hong','유':'yu','전':'jeon','문':'moon','손':'son','양':'yang','백':'baek','허':'heo','남':'nam','심':'shim','노':'noh','하':'ha','곽':'kwak','성':'sung','차':'cha','주':'joo','우':'woo','구':'koo','민':'min','진':'jin','지':'ji','엄':'um','채':'chae','원':'won','천':'cheon','방':'bang','공':'gong','현':'hyun','함':'ham','변':'byun','염':'yeom','여':'yeo','추':'chu','도':'do','소':'so','석':'seok','선':'sun','설':'seol','마':'ma','길':'gil','연':'yeon','위':'wi','표':'pyo','명':'myung','기':'ki','반':'ban','왕':'wang','금':'geum','옥':'ok','육':'yuk','인':'in','맹':'maeng','제':'je','모':'mo','탁':'tak','국':'kuk'}
    value=unicodedata.normalize('NFC',value.strip());result=[]
    if len(value)>=2 and value[0] in surnames:
        result.append(surnames[value[0]]);value=value[1:]
    for char in value:
        code=ord(char)-0xac00
        if 0<=code<11172:
            lead,rest=divmod(code,588);vowel,tail=divmod(rest,28)
            result.append(initials[lead]+vowels[vowel]+finals[tail])
        else:result.append(unicodedata.normalize('NFKD',char).encode('ascii','ignore').decode())
    return re.sub(r'[^a-z0-9.+_-]','',''.join(result).lower())[:80]


def default_author_email(agent):
    return (romanized_name(agent['name']) or agent['id'])+'@moa.local'


def settings(store):
    value=read_setting(store,'project_git',{})
    saved=value.get('authors',{})
    authors={a['id']:(saved.get(a['id']) if saved.get(a['id']) not in (None,'',a['id']+'@moa.local',legacy_author_email(a)) else default_author_email(a)) for a in team_settings(store)['agents']}
    return {'url':value.get('url',''),'branch':value.get('branch',''),'authors':authors,'connected':value.get('connected',False),'updated':value.get('updated',0),'auto_publish':value.get('auto_publish',True)}

def validate_url(value):
    if not isinstance(value,str) or len(value)>500:raise ValueError('Git 저장소 주소를 확인하세요.')
    value=value.strip();url=urlsplit(value)
    if url.scheme=='https' and url.hostname and not url.username and not url.password and not url.query and not url.fragment and url.path not in ('','/') and all(ord(c)>32 for c in value):return value
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+',value) and '..' not in value:return value
    raise ValueError('인증 정보가 없는 HTTPS 주소 또는 git@host:owner/repo.git 주소를 입력하세요.')

def git(args,cwd=None,identity=None,timeout=60):
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'}
    if identity:
        name,email=identity
        env.update(GIT_AUTHOR_NAME=name,GIT_AUTHOR_EMAIL=email,GIT_COMMITTER_NAME=name,GIT_COMMITTER_EMAIL=email)
    empty=Path(__file__).resolve().parent.parent/'runtime/git-empty-hooks';empty.mkdir(parents=True,exist_ok=True)
    command=['git','-c','core.hooksPath='+str(empty),'-c','core.fsmonitor=false','-c','protocol.ext.allow=never',*args]
    try:result=subprocess.run(command,cwd=cwd,env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired:raise ValueError('Git 응답 시간이 초과되었습니다. 연결·업로드 상태를 확인한 뒤 다시 시도하세요.') from None
    except FileNotFoundError:raise ValueError('이 PC에 Git을 먼저 설치하세요. 도구와 스킬에서 설치할 수 있습니다.') from None
    if result.returncode:
        # Credential helpers and remote URLs can contain private values: return a bounded public status.
        raise ValueError('Git 명령에 실패했습니다. 주소·접근 권한·Git 인증 및 브랜치 상태를 확인하세요. (exit '+str(result.returncode)+')')
    return result.stdout.strip()

def folder(store,url):
    target=store.root/'projects'/hashlib.sha256(url.encode()).hexdigest()[:20]/'repository'
    current=store.root
    for part in target.relative_to(store.root).parts:
        current=current/part
        if current.is_symlink() or getattr(current,'is_junction',lambda:False)():raise ValueError('Git 프로젝트 경로에 링크를 사용할 수 없습니다.')
    return target

def save(store,data):
    previous=settings(store);url=validate_url(data.get('url','')) if data.get('url','').strip() else '';branch=data.get('branch','').strip()
    if branch and (branch.startswith('-') or git(['check-ref-format','--branch',branch])!=branch):raise ValueError('브랜치 이름을 확인하세요.')
    authors=data.get('authors',{})
    if not isinstance(authors,dict):raise ValueError('담당자 이메일 형식을 확인하세요.')
    agents={a['id']:a for a in team_settings(store)['agents']};ids=set(agents);clean={}
    for aid,email in authors.items():
        if aid not in ids or not isinstance(email,str) or not re.fullmatch(r'[^\s<>@]{1,80}@[^\s<>@]{1,150}',email):raise ValueError('담당자 이메일을 확인하세요.')
        # Store only overrides so automatic identities follow additions and renames.
        if email not in (aid+'@moa.local',legacy_author_email(agents[aid]),default_author_email(agents[aid])):clean[aid]=email
    value={'auto_publish':data.get('auto_publish',previous['auto_publish']) is True,'url':url,'branch':branch,'authors':clean,'connected':previous['connected'] and previous['url']==url and previous['branch']==branch,'updated':time.time()}
    write_setting(store,'project_git',value);return settings(store)

def connect(store):
    with LOCK:
        value=settings(store);url=validate_url(value['url']);repo=folder(store,url)
        repo.parent.mkdir(parents=True,exist_ok=True)
        if not (repo/'.git').exists():
            if repo.exists():raise ValueError('이전 연결 준비 폴더가 남았습니다. 새 주소로 저장하거나 관리자에게 확인해 주세요.')
            git(['clone','--no-checkout','--',url,str(repo)],timeout=120)
        git(['fetch','origin'],repo,timeout=120)
        remote=git(['ls-remote','--symref','origin','HEAD'],repo)
        branch=value['branch'] or (re.search(r'ref: refs/heads/([^\s]+)',remote).group(1) if re.search(r'ref: refs/heads/([^\s]+)',remote) else 'main')
        if remote:git(['rev-parse','--verify','refs/remotes/origin/'+branch],repo)
        value.update(branch=branch,connected=True,updated=time.time());write_setting(store,'project_git',value)
        return settings(store)

def publish(store,tid,aid):
    import artifacts
    if not isinstance(tid,str) or not re.fullmatch('[a-f0-9]{12}',tid):raise ValueError('작업 ID를 확인하세요.')
    with LOCK:
        value=settings(store)
        if not value['connected']:raise ValueError('먼저 Git 저장소를 연결하세요.')
        with store.connect() as c:row=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
        if not row or row['status']!='completed':raise ValueError('완료된 작업의 결과물만 올릴 수 있습니다.')
        plan=json.loads(row['execution_plan']);agent=next((a for a in plan['agents'] if a['id']==aid),None)
        if not agent:raise ValueError('이 업무에 참여한 담당자를 선택하세요.')
        name=agent['name'];email=value['authors'].get(aid,default_author_email(agent))
        if any(c in name+email for c in '\r\n<>'):raise ValueError('Git 작성자 정보를 확인하세요.')
        import git_publication
        task=dict(row);layout=git_publication.layout(store,task,plan)
        layouts=read_setting(store,'project_git_layouts',{});layouts[tid]=layout;write_setting(store,'project_git_layouts',layouts)
        repo=folder(store,value['url']);branch=layout['branch'];target=repo/layout['path']
        git(['fetch','origin'],repo,timeout=120)
        exists=git(['branch','--list',branch],repo)
        base=git(['branch','-r','--list','origin/'+value['branch']],repo)
        git(['checkout',branch] if exists else ['checkout','-b',branch,'refs/remotes/origin/'+value['branch']] if base else ['checkout','--orphan',branch],repo)
        files=[f for f in artifacts.metadata(store.root,tid,plan)['files'] if f['kind']=='deliverable']
        if not files:raise ValueError('올릴 수 있는 결과 파일이 없습니다.')
        for file in files:
            if file['bytes']>90*1024*1024:raise ValueError('90MB 초과 파일은 Git LFS 구성을 먼저 확인하세요.')
            relative=Path(file['name']).relative_to('deliverables');dest=target/relative
            if any(part.startswith('.') or part.lower() in ('credentials','secrets','id_rsa','id_ed25519') for part in relative.parts):raise ValueError('인증 정보로 보이는 파일은 업로드할 수 없습니다.')
            current=repo
            for part in dest.relative_to(repo).parts:
                current=current/part
                if current.is_symlink() or getattr(current,'is_junction',lambda:False)():raise ValueError('링크 경로에는 결과물을 저장하지 않습니다.')
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists() and dest.stat().st_nlink!=1:raise ValueError('하드링크 파일은 덮어쓰지 않습니다.')
            with artifacts.open_artifact(store.root,tid,file['name'],plan) as (stream,info),dest.open('wb') as out:shutil.copyfileobj(stream,out)
        readme=target/'README.md'
        if not readme.exists():
            reviewer=next((a['name'] for a in plan['agents'] if a['id']==plan['assignment'].get('reviewer')),'담당자')
            lines=['# '+layout['title'],'',layout.get('description',''),'','> 검증을 마친 작업 결과입니다.','', '| 항목 | 내용 |','| --- | --- |','| 구현 | '+name+' |','| 검증 | '+reviewer+' |','','## 결과 파일','']
            lines += ['- ['+str(Path(f['name']).relative_to('deliverables')).replace('\\','/')+']('+str(Path(f['name']).relative_to('deliverables')).replace('\\','/')+')' for f in files]
            readme.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        root_readme=repo/'README.md'
        if not root_readme.exists():
            project=urlsplit(value['url']).path.rstrip('/').split('/')[-1].removesuffix('.git') or 'Team projects'
            root_readme.write_text('# '+project+'\n\n검증을 마친 팀 프로젝트와 결과물을 보관합니다.\n\n## 프로젝트\n\n- ['+layout['title']+']('+layout['path']+'/README.md)\n',encoding='utf-8')
        git(['add','--',layout['path'],'README.md'],repo)
        if git(['diff','--cached','--name-only'],repo):git(['commit','-m',('feat: ' if plan.get('work_kind','code')=='code' else 'docs: ')+layout['title']],repo,(name,email))
        commit=git(['rev-parse','HEAD'],repo)
        actual=git(['show','-s','--format=%an%n%ae',commit],repo).splitlines();name,email=actual[:2]
        git(['push','--set-upstream','origin','HEAD:refs/heads/'+branch],repo,timeout=120)
        receipt={**layout,'url':value['url'].removesuffix('.git')+'/tree/'+branch,'task_id':tid,'branch':branch,'commit':commit,'author':name,'email':email,'files':len(files),'published':time.time()}
        receipts=read_setting(store,'project_git_receipts',{});receipts[tid]=receipt;write_setting(store,'project_git_receipts',receipts)
        store.log(tid,aid,'git_publish',json.dumps(receipt,ensure_ascii=False));return receipt
