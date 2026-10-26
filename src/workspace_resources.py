"""Curated program installation, local instruction skills, and auditable bot requests."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
import organization
from setup_config import read_setting,write_setting,checked_write_setting,team_settings

PROGRAMS=[
 {'id':'python','name':'Python','package':'Python.Python.3.12','icon':'python','category':'개발','description':'Python 코드·자동화·데이터 처리','keywords':['python','파이썬','데이터 분석'],'commands':['python','python3'],'match':['python 3.12'],'site':'https://www.python.org/','guest':'python3.6'},
 {'id':'git','name':'Git','package':'Git.Git','icon':'git','category':'개발','description':'소스 버전 관리와 저장소 작업','keywords':['git','깃','저장소'],'commands':['git'],'match':['git'],'site':'https://git-scm.com/','guest':'git'},
 {'id':'node','name':'Node.js LTS','package':'OpenJS.NodeJS.LTS','icon':'nodejs','category':'개발','description':'JavaScript·웹 프로젝트 실행','keywords':['node','npm','react','웹앱','javascript'],'commands':['node'],'match':['node.js'],'site':'https://nodejs.org/'},
 {'id':'vscode','name':'Visual Studio Code','package':'Microsoft.VisualStudioCode','icon':'vscode','category':'개발','description':'프로젝트와 코드를 편집하는 에디터','keywords':['vscode','코드 편집'],'commands':['code'],'match':['microsoft visual studio code'],'site':'https://code.visualstudio.com/'},
 {'id':'unity','name':'Unity Editor','package':None,'icon':'unity','category':'게임 제작','description':'2D·3D 게임 제작. Unity Hub에서 버전·모듈 선택과 라이선스 준비가 필요합니다.','keywords':['unity','유니티','게임 제작','게임 개발'],'commands':['Unity'],'match':['unity editor'],'site':'https://unity.com/download','manual':True,'setup_note':'Unity Hub 설치 → 로그인·라이선스 확인 → Editor 버전 선택. Windows 실행 환경과 엔진 로그인이 필요합니다. GUI·플레이 모드 검증 연동은 별도이며 Linux Tiny Core에서는 미지원입니다.'},
 {'id':'unity-hub','name':'Unity Hub','package':None,'icon':'unity','category':'게임 제작','description':'Unity Editor 버전·프로젝트·라이선스를 관리하는 설치 도구','keywords':['unity hub','유니티 허브'],'commands':['UnityHub'],'match':['unity hub'],'site':'https://unity.com/download','manual':True,'setup_note':'공식 설치 화면에서 설치합니다. Hub 설치와 Unity Editor 설치는 별개입니다.'},
 {'id':'unreal','name':'Unreal Engine','package':None,'icon':'unrealengine','category':'게임 제작','description':'Blueprint·C++ 기반 3D 게임 제작. Epic Games Launcher에서 엔진 버전을 설치합니다.','keywords':['unreal','언리얼','blueprint','블루프린트','게임 제작','게임 개발'],'commands':['UnrealEditor'],'match':['unreal engine'],'site':'https://www.unrealengine.com/download','manual':True,'setup_note':'Epic Games Launcher 설치 → 로그인 → Unreal Engine에서 버전 선택. Windows 실행 환경과 엔진 로그인이 필요합니다. GUI·플레이 모드 검증 연동은 별도이며 Linux Tiny Core에서는 미지원입니다.'},
 {'id':'epic-launcher','name':'Epic Games Launcher','package':None,'icon':'unrealengine','category':'게임 제작','description':'Unreal Engine 설치·버전·프로젝트를 관리하는 런처','keywords':['epic','에픽'],'commands':['EpicGamesLauncher'],'match':['epic games launcher'],'site':'https://www.unrealengine.com/download','manual':True,'setup_note':'공식 설치 화면에서 설치합니다. 관리자 승인이 필요할 수 있으며 런처 설치만으로 엔진이 설치되지는 않습니다.'},
 {'id':'blender','name':'Blender','package':'BlenderFoundation.Blender','icon':'blender','category':'디자인','description':'3D 모델링·렌더링·애니메이션','keywords':['blender','블렌더','3d','모델링'],'commands':['blender'],'match':['blender'],'site':'https://www.blender.org/'},
 {'id':'affinity','name':'Affinity','package':'Canva.Affinity','icon':'affinity','category':'디자인','description':'사진 보정·이미지 합성·벡터·레이아웃 통합 편집. 무료 Canva 계정으로 최초 로그인합니다.','keywords':['affinity','어피니티','애피니티','이미지 편집','사진 편집','사진 보정','사진 합성'],'commands':['Affinity'],'match':['affinity'],'site':'https://www.affinity.studio/download','setup_note':'WinGet으로 설치한 뒤 최초 실행 시 무료 Canva 계정으로 로그인하세요. Canva AI 기능은 별도 유료 플랜이 필요합니다. 현재 번들 Linux VM에서는 실행할 수 없습니다.'},
 {'id':'gimp','name':'GIMP','package':'GIMP.GIMP.3','icon':'gimp','category':'디자인','description':'이미지 편집과 합성','keywords':['gimp','이미지 편집','사진 합성'],'commands':['gimp','gimp-3.0'],'match':['gimp'],'site':'https://www.gimp.org/'},
 {'id':'inkscape','name':'Inkscape','package':'Inkscape.Inkscape','icon':'inkscape','category':'디자인','description':'SVG·벡터 일러스트 편집','keywords':['inkscape','벡터','svg'],'commands':['inkscape'],'match':['inkscape'],'site':'https://inkscape.org/'},
 {'id':'ffmpeg','name':'FFmpeg','package':'Gyan.FFmpeg','icon':'ffmpeg','category':'미디어','description':'영상·오디오 변환과 추출','keywords':['ffmpeg','영상 변환','오디오 변환'],'commands':['ffmpeg'],'match':['ffmpeg'],'site':'https://ffmpeg.org/'},
 {'id':'sevenzip','name':'7-Zip','package':'7zip.7zip','icon':'archive','category':'유틸리티','description':'압축 파일 열기와 만들기','keywords':['7zip','7-zip','압축'],'commands':['7z'],'match':['7-zip'],'site':'https://www.7-zip.org/'},
 {'id':'slack','name':'Slack','package':'SlackTechnologies.Slack','icon':'slack','category':'협업','description':'팀원과 DM·업무 채널에서 대화','keywords':['slack','슬랙','슬렉'],'commands':['slack'],'match':['slack'],'site':'https://slack.com/downloads/windows'},
]
SKILLS=[
 {'id':'web-design','name':'미니멀 웹 디자인','icon':'design','description':'여백·색·타이포를 일관되게 구성합니다.','keywords':['웹','디자인','ui','html'],'instructions':'Use a minimal layout with clear hierarchy, consistent spacing and the existing brand palette. Include keyboard focus, accessible labels, mobile layout, and reduced-motion support. Verify the actual rendered page before reporting visual success.'},
 {'id':'unity-game','name':'Unity 게임 제작','icon':'unity','description':'C#·씬·프리팹·플레이 테스트를 단계별로 준비합니다.','keywords':['unity','유니티'],'instructions':'Inspect the actual Unity Editor version, project and target platform before changing files. Confirm editor and license availability. Use C# scripts, scenes and prefabs consistently with the existing project. Run actual editor/batch-mode tests if a connected runner supports them; distinguish source creation from verified play-mode behavior. Never claim a Hub-only installation or the bundled Tiny Core VM can execute Unity.'},
 {'id':'unreal-game','name':'Unreal 게임 제작','icon':'unrealengine','description':'Blueprint·C++·에셋과 실제 에디터 검증을 준비합니다.','keywords':['unreal','언리얼','blueprint','블루프린트'],'instructions':'Inspect the actual Unreal Engine version, project and target platform. For Blueprint behavior first express the equivalent complete script or C++ control flow, then verify execution with available tools. Do not infer graph behavior without evidence. Confirm editor availability and use a connected supported runner; do not claim the bundled Tiny Core VM can run Unreal. Distinguish created source/assets from actual compiled and play-tested output.'},
 {'id':'quality-check','name':'실행 기반 QA','icon':'quality','description':'직접 실행하고 실패를 수정·재검증합니다.','keywords':['qa','검증','테스트'],'instructions':'Define observable acceptance checks. Execute meaningful checks and record actual outputs. Fix failures and rerun affected checks. Distinguish real execution from fixtures; never claim unexecuted checks passed.'},
 {'id':'python-work','name':'Python 자동화','icon':'python','description':'작은 스크립트와 데이터 작업을 작성합니다.','keywords':['python','파이썬','자동화','데이터'],'instructions':'Prefer small Python standard-library solutions when practical. Validate input, keep file paths within the task workspace, handle errors explicitly, and test actual output with representative input.'},
 {'id':'slack-team','name':'팀 협업','icon':'slack','description':'결정·진행 상황·근거를 팀원에게 전달합니다.','keywords':['slack','협업','팀'],'instructions':'Address teammates by name. Share concise decisions, observable evidence, questions and the next action. Team members communicate in the Slack work channel; human users use individual bot DMs. Do not claim the recipient saw an image until receipt is confirmed.'},
 {'id':'image-work','name':'이미지 작업','icon':'image','description':'크기·형식·사용 조건을 확인합니다.','keywords':['이미지','사진','그림','아이콘'],'instructions':'Confirm required image dimensions, format and transparency. Check source usage terms. Verify the actual image asset and export dimensions. Do not infer image contents when no image-capable model or viewer is available.'},
 {'id':'research','name':'근거 중심 조사','icon':'research','description':'공식 자료를 우선하고 출처와 한계를 남깁니다.','keywords':['조사','검색','리서치','알아봐'],'instructions':'Use current official primary sources. Separate verified facts from assumptions. Preserve source URLs and explain verification limits. Never fabricate browsing results or quotes when no browsing tool is available.'},
]
FREE_USE_RULE='비용 원칙: 무료 프로그램·무료 플랜·무료 기능만 사용합니다. 유료 구독, 유료 API, 유료 AI 기능, 유료 플러그인·에셋 구매, 결제 등록, 자동 갱신 체험판은 진행하지 않습니다. 무료 조건을 확인할 수 없거나 수익·조직 조건에 따라 비용이 발생하면 실행을 보류하고 무료 대안을 제시합니다. 스킬 추가 자체는 무료이며 스킬이 지시하는 외부 서비스 비용도 이 원칙을 따릅니다.'
COSTS={
 **{key:('free','무료','프로그램 무료 사용 · 별도 유료 서비스·에셋은 제외') for key in ('python','git','node','vscode','unity-hub','epic-launcher','blender','gimp','inkscape','ffmpeg','sevenzip')},
 'affinity':('free_plan','기본 기능 무료','기본 편집 무료 · Canva 계정 필요 · Canva AI 등 유료 기능 제외'),
 'slack':('free_plan','무료 플랜','Free 플랜 기준 · 기록·연동 제한 있음 · 유료 업그레이드 제외'),
 'unity':('conditional_free','조건부 무료','Unity Personal 자격 조건 확인 필요 · 조건 밖에서는 유료 · 자동 설치 제외'),
 'unreal':('conditional_free','조건부 무료','용도·매출에 따른 로열티·좌석 비용 조건 확인 필요 · 자동 설치 제외'),
}
for program in PROGRAMS:
 kind,label,note=COSTS.get(program['id'],('unknown','무료 여부 미확인','비용 확인 전 설치 제외'))
 program.update(cost_kind=kind,cost_label=label,cost_note=note,cost_source={'unity':'https://unity.com/products/unity-personal','unreal':'https://www.unrealengine.com/license','slack':'https://slack.com/pricing/free'}.get(program['id'],program['site']))
PROGRAM_BY_ID={p['id']:p for p in PROGRAMS}
DEFAULT_POLICY={'auto_programs':False,'auto_skills':True,'allowed_programs':[],'package_terms':False,'source_terms':False,'global_instructions':'','updated':0}

class PendingResource(ValueError):pass

def policy(store):return {**DEFAULT_POLICY,**read_setting(store,'resource_policy',{})}

def skills(store):
    saved=read_setting(store,'resource_skills',{})
    return [{**s,**saved.get(s['id'],{}),'builtin':True} for s in SKILLS]+[{**s,'builtin':False} for key,s in saved.items() if key not in {s['id'] for s in SKILLS}]

def save_policy(store,data):
    old=policy(store);value={**old,**data}
    for key in ('auto_programs','auto_skills','package_terms','source_terms'):
        if type(value.get(key)) is not bool:raise ValueError('자동 설치 설정은 참 또는 거짓이어야 합니다.')
    allowed=value.get('allowed_programs')
    if not isinstance(allowed,list) or any(not isinstance(p,str) or p not in PROGRAM_BY_ID for p in allowed):raise ValueError('등록된 프로그램만 자동 설치할 수 있습니다.')
    text=value.get('global_instructions')
    if not isinstance(text,str) or len(text)>12000:raise ValueError('전역 지시 사항은 12,000자 이내로 입력하세요.')
    clean={k:value[k] for k in DEFAULT_POLICY};clean['allowed_programs']=list(dict.fromkeys(allowed));clean['updated']=time.time()
    checked_write_setting(store,'resource_policy',clean,data);return policy(store)

def save_skill(store,data):
    key=data.get('id') or 'custom-'+uuid.uuid4().hex[:10]
    if not isinstance(key,str) or not re.fullmatch('[a-z][a-z0-9-]{0,50}',key):raise ValueError('스킬 ID 오류')
    current=next((s for s in skills(store) if s['id']==key),{})
    value={**current,**data,'id':key}
    for field,limit in [('name',60),('description',300),('instructions',12000)]:
        if not isinstance(value.get(field),str) or not value[field].strip() or len(value[field])>limit:raise ValueError(f'{field} 항목을 확인하세요. 최대 {limit}자입니다.')
    agents=value.get('agents',[])
    if not isinstance(agents,list) or any(not isinstance(a,str) or a not in {a['id'] for a in team_settings(store)['agents']} for a in agents):raise ValueError('스킬을 사용할 팀원을 확인하세요.')
    if type(value.get('installed',True)) is not bool:raise ValueError('스킬 활성 상태 오류')
    clean={k:value[k] for k in ('id','name','description','instructions')};clean.update(agents=list(dict.fromkeys(agents)),installed=value.get('installed',True),icon=current.get('icon','skill'))
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT value FROM settings WHERE key='resource_skills'").fetchone();saved=json.loads(row[0]) if row else {};saved[key]=clean
        c.execute("INSERT INTO settings(key,value) VALUES('resource_skills',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(saved,ensure_ascii=False),))
    return clean

def frozen_context(store):
    from peer_scores import competition_instruction
    import team_growth
    growth=[]
    for a in team_settings(store)['agents']:
        recent=team_growth.detail(store,a['id'])[:1]
        growth.append({'agent_id':a['id'],'goal':recent[0]['goal'][:100] if recent else '', 'prior_metrics':recent[0].get('metrics',{}) if recent else {}})
    growth_text='\n리더 개인 개선 목표·전후 근거: '+json.dumps(growth,ensure_ascii=False,separators=(',',':'))
    return {'global_instructions':policy(store)['global_instructions']+'\n\n'+competition_instruction(store)+growth_text+'\n\n'+FREE_USE_RULE+'\n\n'+organization.instruction(team_settings(store)['agents']),'skills':[copy.deepcopy(s) for s in skills(store) if s.get('installed')]}

def context(task):
    try:config=json.loads(task.get('execution_plan') or '{}').get('workspace_context',{})
    except (ValueError,TypeError):config={}
    actor=task.get('agent',{}).get('id')
    active=[{'name':s['name'],'instructions':s['instructions']} for s in config.get('skills',[]) if not s.get('agents') or actor in s['agents']]
    return {'global_instructions':config.get('global_instructions',''),'skills':active}

def recommend(store,title,target='host'):
    if not isinstance(title,str) or len(title)>8000:raise ValueError('작업 내용은 8,000자 이내로 입력하세요.')
    text=title.lower()
    explicit_unity=any(k in text for k in ('unity','유니티'));explicit_unreal=any(k in text for k in ('unreal','언리얼','blueprint','블루프린트'))
    return {'programs':[p['id'] for p in PROGRAMS if any(k in text for k in p['keywords']) and not (p['id']=='unity' and explicit_unreal and not explicit_unity) and not (p['id']=='unreal' and explicit_unity and not explicit_unreal)],
            'skills':[s['id'] for s in skills(store) if any(k in text for k in s.get('keywords',[]))],
            'target':target,'basis':'작업 내용의 키워드 기준 추천 · 실제 필요 여부를 확인한 뒤 설치하세요. 게임 엔진은 Unity·Unreal 중 사용할 엔진을 선택하세요.'}

def installed_programs():
    entries=[]
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE,winreg.HKEY_CURRENT_USER):
            for location in (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
                try:
                    with winreg.OpenKey(hive,location) as parent:
                        for n in range(winreg.QueryInfoKey(parent)[0]):
                            try:
                                with winreg.OpenKey(parent,winreg.EnumKey(parent,n)) as key:
                                    def get(name):
                                        try:return str(winreg.QueryValueEx(key,name)[0])[:300]
                                        except OSError:return ''
                                    name=get('DisplayName')
                                    if name:entries.append({'name':name,'version':get('DisplayVersion'),'publisher':get('Publisher'),'evidence':'Windows 설치 레지스트리'})
                            except OSError:continue
                except OSError:continue
        # Affinity uses MSIX; the desktop Uninstall registry alone misses it.
        try:
            query=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',"Get-AppxPackage -Name Canva.Affinity | Select-Object Name,Version | ConvertTo-Json -Compress"],capture_output=True,encoding='utf-8',errors='replace',timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            packages=json.loads(query.stdout) if query.returncode==0 and query.stdout.strip() else []
            for package in ([packages] if isinstance(packages,dict) else packages or []):
                if package.get('Name')=='Canva.Affinity':entries.append({'name':'Affinity','version':str(package.get('Version','')),'publisher':'Canva','evidence':'Windows MSIX 패키지 등록 확인'})
        except (OSError,ValueError,TypeError,subprocess.TimeoutExpired):pass
    unique={e['name'].casefold():e for e in entries}
    return sorted(unique.values(),key=lambda e:e['name'].lower())

def engine_installs():
    result={}
    if os.name!='nt':return result
    base=Path(os.environ.get('ProgramFiles','C:/Program Files'))
    folder=base/'Unity/Hub/Editor'
    if folder.is_dir():
        for exe in sorted(folder.glob('*/Editor/Unity.exe'),reverse=True)[:50]:
            if exe.is_file():result['unity']={'path':str(exe),'version':exe.parents[1].name,'evidence':'실제 Unity Editor 실행 파일 확인'};break
    for pid,paths in {'unity-hub':[base/'Unity Hub/Unity Hub.exe'], 'epic-launcher':[Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/('Epic Games/Launcher/Portal/Binaries/'+arch+'/EpicGamesLauncher.exe') for arch in ('Win64','Win32')]}.items():
        for exe in paths:
            if exe.is_file():result[pid]={'path':str(exe),'evidence':'실제 런처 실행 파일 확인'};break
    launcher=Path(os.environ.get('ProgramData','C:/ProgramData'))/'Epic/UnrealEngineLauncher/LauncherInstalled.dat'
    try:
        if launcher.stat().st_size<1024*1024:
            for item in json.loads(launcher.read_text(encoding='utf-8-sig')).get('InstallationList',[]):
                if not str(item.get('AppName','')).startswith('UE_'):continue
                exe=Path(item.get('InstallLocation',''))/'Engine/Binaries/Win64/UnrealEditor.exe'
                if exe.is_file():result['unreal']={'path':str(exe),'version':str(item.get('AppName','')).removeprefix('UE_'),'evidence':'Epic 설치 기록과 실제 Unreal Editor 실행 파일 확인'};break
    except (OSError,ValueError,TypeError):pass
    return result


def inventory():
    entries=installed_programs();programs=[];engines=engine_installs()
    for p in PROGRAMS:
        found=next((e for e in entries if any(e['name'].lower()==m or e['name'].lower().startswith(m+' ') or e['name'].lower().startswith(m+' (') for m in p['match'])),None)
        executable=next((shutil.which(cmd) for cmd in p['commands'] if shutil.which(cmd)),None)
        # The bundled Python is a MOA runtime, not a system Python installation.
        if executable and 'windowsapps' in executable.lower() and p['id']=='python':executable=None
        engine=engines.get(p['id'])
        if engine:executable=engine['path']
        if p['id']=='unity' and not found:found=next((e for e in entries if re.match(r'^unity \d',e['name'],re.I)),None)
        programs.append({**p,'installed':bool(found or executable),'version':found['version'] if found else engine.get('version','') if engine else '',
                         'evidence':found['evidence'] if found else engine['evidence'] if engine else 'PATH 실행 파일' if executable else '설치 근거 없음','executable':executable or ''})
    return {'programs':programs,'installed':entries,'checked_at':time.time(),'winget':bool(shutil.which('winget')),'target':'host'}

class Resources:
    def __init__(self,store,demo=False):
        self.store=store;self.demo=demo;self.lock=threading.Lock();self.worker=None;self.cached=None
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS resource_jobs(id TEXT PRIMARY KEY,program TEXT,target TEXT,state TEXT,actor TEXT,task_id TEXT,created REAL,updated REAL,message TEXT,exit_code INTEGER)')
            c.execute('CREATE TABLE IF NOT EXISTS resource_task_skills(task_id TEXT,skill_id TEXT,PRIMARY KEY(task_id,skill_id))')
            if 'content' not in {r[1] for r in c.execute('PRAGMA table_info(resource_task_skills)')}:c.execute("ALTER TABLE resource_task_skills ADD COLUMN content TEXT DEFAULT ''")
    def recover(self):
        # Called only after the app owns its worker lock.
        with self.store.connect() as c:c.execute("UPDATE resource_jobs SET state='interrupted',message='이전 설치가 중단되었습니다. 설치 목록을 새로 확인한 뒤 재시도하세요.',updated=? WHERE state IN ('queued','running')",(time.time(),))

    def snapshot(self,refresh=False):
        with self.lock:
            if self.cached is None or refresh:self.cached=inventory()
            result=copy.deepcopy(self.cached)
        with self.store.connect() as c:result['jobs']=[dict(r) for r in c.execute('SELECT * FROM resource_jobs ORDER BY created DESC LIMIT 40')]
        result.update(skills=skills(self.store),policy=policy(self.store),demo_only=self.demo,guest=read_setting(self.store,'resource_guest_inventory',{}))
        try:
            session=json.loads((self.store.root/'vm/session.json').read_text(encoding='utf-8'))
            result['guest_session']=session.get('name','');result['guest_network']=session.get('config',{}).get('network',False)
        except (OSError,ValueError):result['guest_session']='';result['guest_network']=False
        import execution_targets
        result['execution']=execution_targets.snapshot(self.store)
        for item in result['programs']:
            item['environment_support']=execution_targets.support(item,result['execution']['selected']['location'],result['execution']['selected']['os'])
        return result

    def request(self,pid,actor='user',task_id='',target='host'):
        if pid not in PROGRAM_BY_ID:raise ValueError('등록된 프로그램만 설치할 수 있습니다.')
        if target not in ('host','guest'):raise ValueError('설치 대상을 확인하세요.')
        if self.demo:raise ValueError('데모 실행에서는 실제 프로그램을 설치하지 않습니다.')
        import execution_targets,platform
        from vm_runtime import settings as vm_settings
        target_os=vm_settings(self.store).get('guest_os','linux') if target=='guest' else ('windows' if platform.system()=='Windows' else platform.system().lower())
        capability=execution_targets.support(PROGRAM_BY_ID[pid],'shared_vm' if target=='guest' else 'host',target_os)
        if not capability['supported']:raise ValueError(capability['reason'])
        p=policy(self.store)
        if PROGRAM_BY_ID[pid].get('cost_kind') not in ('free','free_plan'):state='action_required';message=PROGRAM_BY_ID[pid]['cost_note']+' '+PROGRAM_BY_ID[pid]['cost_source']
        elif actor!='user' and (not p['auto_programs'] or pid not in p['allowed_programs']):state='approval_required';message='봇의 설치 요청입니다. 허용 목록과 자동 설치 설정을 확인하세요.'
        elif target=='host' and PROGRAM_BY_ID[pid].get('manual'):state='action_required';message=PROGRAM_BY_ID[pid]['setup_note']+' '+PROGRAM_BY_ID[pid]['site']
        elif target=='guest':state='action_required';message='VM 내부 설치 요청입니다. 해당 작업의 VM 단계에서 설치합니다. VM 네트워크와 패키지 지원을 먼저 확인하세요.'
        elif not p['package_terms'] or not p['source_terms']:state='approval_required';message='프로그램·WinGet 저장소 이용 약관 확인이 필요합니다. 설치 설정에서 확인 후 설치를 다시 누르세요.'
        elif not shutil.which('winget'):state='action_required';message='Windows 앱 설치 관리자(WinGet)가 필요합니다.'
        else:state='queued';message='설치 대기 중'
        now=time.time()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute("SELECT * FROM resource_jobs WHERE program=? AND target=? AND state IN ('queued','running') ORDER BY created DESC LIMIT 1",(pid,target)).fetchone()
            if old:return dict(old)
            # Reuse unresolved requests instead of creating one on every task poll.
            old=c.execute("SELECT * FROM resource_jobs WHERE program=? AND target=? AND actor=? AND task_id=? AND state IN ('approval_required','action_required') ORDER BY created DESC LIMIT 1",(pid,target,actor,task_id)).fetchone()
            jid=old['id'] if old else uuid.uuid4().hex
            c.execute('INSERT INTO resource_jobs VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET state=excluded.state,message=excluded.message,updated=excluded.updated',(jid,pid,target,state,actor,task_id,now,now,message))
        if state=='queued':self.start()
        return {'id':jid,'program':pid,'target':target,'state':state,'message':message}

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():return
            self.worker=threading.Thread(target=self.loop,daemon=True);self.worker.start()

    def update(self,jid,state,message,code=None):
        with self.store.connect() as c:c.execute('UPDATE resource_jobs SET state=?,message=?,exit_code=?,updated=? WHERE id=?',(state,str(message)[-4000:],code,time.time(),jid))

    def loop(self):
        while True:
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT * FROM resource_jobs WHERE state='queued' AND target='host' ORDER BY created LIMIT 1").fetchone()
                if not row:return
                job=dict(row);c.execute("UPDATE resource_jobs SET state='running',updated=? WHERE id=?",(time.time(),job['id']))
            try:
                p=policy(self.store)
                if job['actor']!='user' and (not p['auto_programs'] or job['program'] not in p['allowed_programs']):raise ValueError('자동 설치 허용이 취소되었습니다.')
                if not p['package_terms'] or not p['source_terms']:raise ValueError('설치 약관 확인이 취소되었습니다.')
                current=self.snapshot(refresh=True)
                if next(x for x in current['programs'] if x['id']==job['program'])['installed']:
                    self.update(job['id'],'completed','이미 설치되어 있습니다. 설치 근거를 다시 확인했습니다.',0);continue
                if PROGRAM_BY_ID[job['program']].get('cost_kind') not in ('free','free_plan'):raise ValueError('무료 사용 확인이 필요한 프로그램은 자동 설치하지 않습니다.')
                command=[shutil.which('winget'),'install','--id',PROGRAM_BY_ID[job['program']]['package'],'--exact','--source','winget','--scope','user','--silent','--disable-interactivity','--no-upgrade','--accept-package-agreements','--accept-source-agreements']
                result=subprocess.run(command,capture_output=True,encoding='utf-8',errors='replace',timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                current=self.snapshot(refresh=True);found=next(x for x in current['programs'] if x['id']==job['program'])['installed']
                self.update(job['id'],'completed' if found else 'failed',('설치 근거 재확인 완료.\n' if found else '설치 완료를 확인하지 못했습니다.\n')+(result.stdout+result.stderr)[-3500:],result.returncode)
            except subprocess.TimeoutExpired:self.update(job['id'],'interrupted','설치 응답 시간이 초과되었습니다. 설치 상태를 새로 확인한 뒤 재시도하세요.')
            except Exception as error:self.update(job['id'],'failed',str(error))

    def bot_skill(self,task,sid,actor):
        skill=next((s for s in skills(self.store) if s['id']==sid),None)
        if not skill:raise ValueError('등록되지 않은 스킬입니다.')
        now=time.time()
        if not skill.get('installed') and not policy(self.store)['auto_skills']:
            with self.store.connect() as c:
                if not c.execute("SELECT 1 FROM resource_jobs WHERE program=? AND actor=? AND task_id=? AND state='approval_required'",('skill:'+sid,actor,task['id'])).fetchone():c.execute('INSERT INTO resource_jobs VALUES(?,?,?,?,?,?,?,?,?,NULL)',(uuid.uuid4().hex,'skill:'+sid,'instructions','approval_required',actor,task['id'],now,now,'스킬 자동 추가가 꺼져 있습니다. 스킬을 추가한 뒤 작업을 재개하세요.'))
            raise PendingResource('스킬 자동 추가가 꺼져 있습니다. 도구와 스킬에서 추가해 주세요: '+skill['name'])
        if skill.get('agents') and actor not in skill['agents']:raise PendingResource('이 팀원에게 배정되지 않은 스킬입니다: '+skill['name'])
        if not skill.get('installed'):save_skill(self.store,{**skill,'installed':True})
        with self.store.connect() as c:
            added=c.execute('INSERT OR IGNORE INTO resource_task_skills(task_id,skill_id,content) VALUES(?,?,?)',(task['id'],sid,json.dumps(skill,ensure_ascii=False))).rowcount
            c.execute("UPDATE resource_jobs SET state='completed',message='스킬 추가와 작업 지침 반영을 확인했습니다.',updated=? WHERE program=? AND actor=? AND task_id=? AND state='approval_required'",(now,'skill:'+sid,actor,task['id']))
            if added:c.execute('INSERT INTO resource_jobs VALUES(?,?,?,?,?,?,?,?,?,NULL)',(uuid.uuid4().hex,'skill:'+sid,'instructions','completed',actor,task['id'],now,now,'작업에 스킬 지침을 반영했습니다.'))
        self.store.log(task['id'],actor,'resource_skill',skill['name']+' 스킬을 추가했습니다.')
        return {'name':skill['name'],'instructions':skill['instructions']}

    def task_context(self,task):
        result=context(task)
        from peer_scores import competition,summary,ACE_POLICY
        ranking=competition(self.store)['ranking']
        result['global_instructions']=re.sub(r'호감도 에이스 경쟁: [^\n]*',ACE_POLICY,result['global_instructions'])
        result['global_instructions']=re.sub(r'\n현재 (?:호감도|업무) 순위: [^\n]*','\n현재 업무 순위: '+json.dumps(ranking,ensure_ascii=False),result['global_instructions'])
        result['global_instructions']=re.sub(r'\n현재 업무 점수: [^\n]*','\n현재 업무 점수: '+json.dumps(summary(self.store),ensure_ascii=False),result['global_instructions'])
        result['global_instructions']=re.sub(r'\n리더 개인 개선 목표·전후 근거: [^\n]*','',result['global_instructions'])
        with self.store.connect() as c:requested=[json.loads(r['content']) for r in c.execute("SELECT content FROM resource_task_skills WHERE task_id=? AND content!=''",(task['id'],))]
        actor=task.get('agent',{}).get('id')
        for s in requested:
            if not s.get('agents') or actor in s['agents']:result['skills'].append({'name':s['name'],'instructions':s['instructions']})
        return result

    def bot_program(self,task,pid,actor,vm=None,role='developer'):
        if pid not in PROGRAM_BY_ID:raise ValueError('등록되지 않은 프로그램입니다.')
        target='guest' if json.loads(task.get('execution_plan') or '{}').get('environment',{}).get('guest_execution') else 'host'
        if target=='guest' and pid=='slack':return {'available':True,'installation_required':False,'target':'orchestrator','message':'Slack 대화와 이미지 전송은 모아가 처리합니다. VM에 Slack을 설치하지 말고 담당 작업을 진행하세요.'}
        if target=='host' and next(p for p in self.snapshot(refresh=True)['programs'] if p['id']==pid)['installed']:return {'installed':True,'target':'host'}
        job=self.request(pid,actor,task['id'],target)
        self.store.log(task['id'],actor,'resource_request',json.dumps(job,ensure_ascii=False))
        if target=='guest' and vm and vm.session().get('config',{}).get('guest_os')=='windows':
            return self.windows_program(task,pid,actor,vm,role,job)
        if target=='guest':
            p=policy(self.store);package=PROGRAM_BY_ID[pid].get('guest')
            known=read_setting(self.store,'resource_guest_inventory',{})
            if vm and known.get('session')==vm.session().get('name') and any(r['id']==pid and r['installed'] for r in known.get('programs',[])):
                self.update(job['id'],'completed','현재 VM 세션에서 실행 파일을 확인했습니다.',0);return {'installed':True,'target':'guest'}
            if not p['auto_programs'] or pid not in p['allowed_programs']:raise PendingResource('VM 프로그램 자동 설치를 허용해 주세요: '+PROGRAM_BY_ID[pid]['name'])
            if not package:raise PendingResource('현재 번들 VM에 등록된 설치 패키지가 없습니다: '+PROGRAM_BY_ID[pid]['name'])
            session=vm.session() if vm else {}
            if not session.get('config',{}).get('network'):raise PendingResource('VM 네트워크가 꺼져 있어 설치를 보류했습니다. 작업 환경에서 네트워크를 설정하고 VM을 재시작한 뒤 새 작업을 등록해 주세요.')
            command=f"command -v {PROGRAM_BY_ID[pid]['commands'][0]} >/dev/null || su tc -c 'tce-load -wi {package}'; command -v {PROGRAM_BY_ID[pid]['commands'][0]}"
            self.update(job['id'],'running','실제 VM 내부 설치 실행 중')
            try:result=vm.command(task['id'],role,command)
            except Exception as error:self.update(job['id'],'interrupted',str(error));raise PendingResource('VM 설치 결과를 확인할 수 없습니다. 기록을 확인하고 수동 재개해 주세요.') from None
            self.update(job['id'],'completed' if result['exit_code']==0 else 'failed',result['output'],result['exit_code'])
            if result['exit_code']!=0:raise PendingResource('VM 설치에 실패했습니다. 설치 기록을 확인해 주세요.')
            return {'installed':True,'target':'guest','evidence':result}
        if job['state'] not in ('queued','running'):raise PendingResource(job['message'])
        deadline=time.monotonic()+910
        while time.monotonic()<deadline:
            with self.store.connect() as c:
                active=c.execute('SELECT status FROM tasks WHERE id=?',(task['id'],)).fetchone()
                row=c.execute('SELECT * FROM resource_jobs WHERE id=?',(job['id'],)).fetchone()
            if active and active[0] not in ('running','queued'):raise PendingResource('작업이 중단되었습니다. 설치 기록을 확인해 주세요.')
            if row['state']=='completed':return {'installed':True,'target':'host'}
            if row['state'] not in ('queued','running'):raise PendingResource(row['message'])
            time.sleep(.5)
        raise PendingResource('프로그램 설치 결과 대기 시간이 초과되었습니다.')

    def guest_scan(self,task,vm,role,force=False):
        session=vm.session();old=read_setting(self.store,'resource_guest_inventory',{})
        if not force and old.get('session')==session.get('name') and old.get('checked_at',0)>time.time()-300:return old
        if session.get('config',{}).get('guest_os')=='windows':
            return self.windows_scan(task,vm,role)
        commands=list(dict.fromkeys(cmd for p in PROGRAMS for cmd in p['commands']))
        command='for c in '+' '.join(commands)+'; do p=$(command -v $c); echo $c:$p; done'
        result=vm.command(task['id'],role,command)
        if result['exit_code']!=0:raise ValueError('VM 프로그램 목록을 확인하지 못했습니다.')
        paths={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in result['output'].splitlines() if ':' in line and line.split(':',1)[0] in commands}
        found={p['id']:next((paths[cmd] for cmd in p['commands'] if paths.get(cmd)), '') for p in PROGRAMS}
        value={'session':session.get('name'),'checked_at':time.time(),'programs':[{'id':p['id'],'installed':bool(found.get(p['id'])),'executable':found.get(p['id'],''),'evidence':'실제 VM command -v 확인'} for p in PROGRAMS]}
        write_setting(self.store,'resource_guest_inventory',value)
        self.store.log(task['id'],task['agent']['id'],'resource_inventory',json.dumps(value,ensure_ascii=False));return value

    def guest_prepare(self,task,vm,role):
        """Run user-requested VM installs only while this task owns the desktop."""
        known=self.guest_scan(task,vm,role)
        if vm.session().get('config',{}).get('guest_os')=='windows':
            with self.store.connect() as c:jobs=[dict(r) for r in c.execute("SELECT * FROM resource_jobs WHERE target='guest' AND actor='user' AND state='action_required'")]
            for job in jobs:self.windows_program(task,job['program'],'user',vm,role,job)
            return
        with self.store.connect() as c:jobs=[dict(r) for r in c.execute("SELECT * FROM resource_jobs WHERE target='guest' AND actor='user' AND state='action_required'")]
        for job in jobs:
            pid=job['program'];package=PROGRAM_BY_ID[pid].get('guest')
            if not package:self.update(job['id'],'failed','번들 Tiny Core VM에 등록된 설치 패키지가 없습니다.');continue
            if any(p['id']==pid and p['installed'] for p in known['programs']):self.update(job['id'],'completed','VM 실행 파일 확인 완료.',0);continue
            if not vm.session().get('config',{}).get('network'):
                self.update(job['id'],'action_required','VM 네트워크가 꺼져 있습니다. 네트워크 설정과 VM 재시작 후 다음 작업에서 설치합니다.');continue
            if not policy(self.store)['package_terms']:
                self.update(job['id'],'approval_required','패키지 설치 조건을 먼저 확인해 주세요.');continue
            command=f"su tc -c 'tce-load -wi {package}'; command -v {PROGRAM_BY_ID[pid]['commands'][0]}"
            self.update(job['id'],'running','사용자가 요청한 프로그램을 실제 VM에 설치 중')
            try:
                result=vm.command(task['id'],role,command)
                self.update(job['id'],'completed' if result['exit_code']==0 else 'failed',result['output'],result['exit_code'])
            except Exception as error:self.update(job['id'],'interrupted',str(error))
            known=self.guest_scan(task,vm,role,True)

    def windows_scan(self,task,vm,role):
        commands=list(dict.fromkeys(cmd for p in PROGRAMS for cmd in p['commands']))
        source='$rows=@('+','.join("'"+c+"'" for c in commands)+");$out=@{};foreach($name in $rows){$cmd=Get-Command $name -ErrorAction SilentlyContinue|Select-Object -First 1;$out[$name]=if($cmd){$cmd.Source}else{''}};ConvertTo-Json $out -Compress"
        result=vm.command(task['id'],role,source)
        if result['exit_code']!=0:raise ValueError('Windows VM 설치 목록 조회 실패')
        paths=json.loads(result.get('stdout',result['output']));value={'session':vm.session()['name'],'checked_at':time.time(),'guest_os':'windows','programs':[{'id':p['id'],'installed':any(paths.get(c) for c in p['commands']),'executable':next((paths[c] for c in p['commands'] if paths.get(c)),''),'evidence':'Windows VM Get-Command 확인'} for p in PROGRAMS]}
        write_setting(self.store,'resource_guest_inventory',value);return value

    def windows_program(self,task,pid,actor,vm,role,job):
        program=PROGRAM_BY_ID[pid];p=policy(self.store)
        known=self.windows_scan(task,vm,role)
        if any(r['id']==pid and r['installed'] for r in known['programs']):
            self.update(job['id'],'completed','Windows VM 실행 파일 확인',0);return {'installed':True,'target':'guest'}
        if program.get('manual') or not program.get('package') or program.get('cost_kind') not in ('free','free_plan'):raise PendingResource('Windows VM에서 직접 로그인·사용 조건 확인 후 설치하세요: '+program['name'])
        if actor!='user' and (not p['auto_programs'] or pid not in p['allowed_programs']):raise PendingResource('Windows VM 자동 설치 허용이 필요합니다: '+program['name'])
        if not p['package_terms'] or not p['source_terms']:raise PendingResource('설치·저장소 약관 확인이 필요합니다.')
        if not vm.session().get('config',{}).get('network'):raise PendingResource('Windows VM 네트워크를 켜 주세요.')
        self.update(job['id'],'running','Windows VM WinGet 설치 중')
        try:
            result=vm.command(task['id'],role,'winget install --id '+program['package']+' --exact --source winget --scope user --silent --disable-interactivity --no-upgrade --accept-package-agreements --accept-source-agreements',timeout=900)
            found=self.windows_scan(task,vm,role)
            installed=any(r['id']==pid and r['installed'] for r in found['programs'])
            self.update(job['id'],'completed' if installed else 'failed',result['output'],result['exit_code'])
            if not installed:raise PendingResource('Windows VM 설치 후 실행 파일 미확인. PATH 갱신 또는 직접 설치가 필요합니다.')
            return {'installed':True,'target':'guest','evidence':result}
        except Exception as error:
            self.update(job['id'],'interrupted',str(error));raise

    def fulfill(self,task,request,actor,vm=None,role='developer'):
        if not isinstance(request,str) or ':' not in request:raise ValueError('프로그램·스킬 요청 형식 오류')
        kind,key=request.split(':',1)
        if kind=='skill':return self.bot_skill(task,key,actor)
        if kind=='program':return self.bot_program(task,key,actor,vm,role)
        raise ValueError('지원하지 않는 준비 요청입니다.')

    def available(self,guest=False):
        return {'programs':[{'id':'program:'+p['id'],'name':p['name'],'target':'host; guest only when a supported package and network are available'} for p in PROGRAMS if not guest or p.get('guest') or read_setting(self.store,'vm',{}).get('guest_os')=='windows'],
                'skills':[{'id':'skill:'+s['id'],'name':s['name'],'description':s['description']} for s in skills(self.store)]}
