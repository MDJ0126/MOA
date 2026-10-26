"""Optional first-run survey; recommendations never install software implicitly."""
import json
import hashlib
import time
from setup_config import read_setting,team_settings,PRESETS,validate_team,hardware
import workspace_resources as wr

TASKS=[{'id':'web','name':'웹·앱 개발','description':'웹사이트, 대시보드, 작은 앱','programs':['git','node','vscode'],'skills':['web-design','quality-check']},
       {'id':'game','name':'게임 제작','description':'Unity·Unreal 게임 프로젝트','programs':['git','vscode'],'skills':['quality-check']},
       {'id':'data','name':'데이터·업무 자동화','description':'파일 정리, 데이터 분석, 반복 작업','programs':['python','vscode'],'skills':['python-work','quality-check']},
       {'id':'design','name':'이미지·3D 디자인','description':'그림, 편집, 모델링','programs':['affinity','blender','gimp','inkscape'],'skills':['image-work']},
       {'id':'research','name':'조사·문서 정리','description':'자료 조사, 요약과 보고','programs':[],'skills':['research','quality-check']}]

def status(store):
    saved=read_setting(store,'onboarding',{})
    with store.connect() as c:has_work=c.execute('SELECT 1 FROM tasks LIMIT 1').fetchone() is not None
    team=team_settings(store)
    return {'needed':not saved and not team.get('configured') and not team.get('recovery_required') and not has_work,
            'saved':saved,'tasks':[{k:v for k,v in t.items() if k in ('id','name','description')} for t in TASKS]}

def recommend(store,data):
    choices=data.get('tasks',[]);engine=data.get('engine','undecided')
    if not isinstance(choices,list) or not 1<=len(choices)<=5 or any(type(v) is not str or v not in {t['id'] for t in TASKS} for v in choices):raise ValueError('주로 할 업무를 한 가지 이상 골라 주세요.')
    if engine not in ('unity','unreal','undecided'):raise ValueError('게임 엔진 선택을 확인하세요.')
    programs=[];skills=[]
    for t in TASKS:
        if t['id'] in choices:programs+=t['programs'];skills+=t['skills']
    if 'game' in choices and engine!='undecided':
        programs += ['unity-hub','unity'] if engine=='unity' else ['epic-launcher','unreal']
        skills.append('unity-game' if engine=='unity' else 'unreal-game')
    inventory={p['id']:p for p in wr.inventory()['programs']}
    programs=[inventory[p] for p in dict.fromkeys(programs)]
    available={s['id']:s for s in wr.skills(store)}
    skill_items=[available[k] for k in dict.fromkeys(skills)]
    preset='standard'
    team=team_settings(store)
    with store.connect() as c:has_work=c.execute('SELECT 1 FROM tasks LIMIT 1').fetchone() is not None
    return {'tasks':list(dict.fromkeys(choices)),'engine':engine,'programs':programs,'skills':skill_items,
            'team':next(p for p in PRESETS if p['id']==preset),'can_apply_team':not team.get('configured') and not team.get('recovery_required') and not has_work,
            'note':'무료 사용을 기준으로 스킬·팀 구성을 적용합니다. 유료 기능·구독은 제외합니다. 프로그램은 설치 후보이며 버튼을 누르기 전에는 설치하지 않습니다.',
            'game_note':'Unity·Unreal은 조건부 무료이므로 용도·매출·라이선스 자격을 먼저 확인하세요. 유료 플랜은 진행하지 않습니다. 현재 엔진 실행 연동은 미연결입니다.' if 'game' in choices else ''}

def save(store,data):
    action=data.get('action')
    if action in ('skip','manual'):
        value={**read_setting(store,'onboarding',{}),'state':'skipped' if action=='skip' else 'manual','updated':time.time()}
        value.pop('signature',None)
        with store.connect() as c:c.execute("INSERT INTO settings VALUES('onboarding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(value),))
        return value
    if action!='apply':raise ValueError('초기 설정 동작을 확인하세요.')
    rec=recommend(store,data)
    selected=data.get('programs',[]);chosen_skills=data.get('skills',[]);apply_team=data.get('apply_team',False)
    if type(apply_team) is not bool:raise ValueError('팀 구성 선택 오류')
    for value,allowed in ((selected,{p['id'] for p in rec['programs']}),(chosen_skills,{s['id'] for s in rec['skills']})):
        if not isinstance(value,list) or any(type(x) is not str or x not in allowed for x in value):raise ValueError('추천 항목 안에서 선택해 주세요.')
    signature=hashlib.sha256(json.dumps([rec['tasks'],rec['engine'],sorted(set(selected)),sorted(set(chosen_skills)),apply_team],ensure_ascii=False).encode()).hexdigest()
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        previous=c.execute("SELECT value FROM settings WHERE key='onboarding'").fetchone()
        if previous and json.loads(previous[0]).get('signature')==signature:return json.loads(previous[0])
        row=c.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()
        existing=json.loads(row[0]) if row else {}
        if apply_team:
            if existing.get('configured') or c.execute('SELECT 1 FROM tasks LIMIT 1').fetchone():raise ValueError('기존 팀은 초기 설문에서 변경하지 않습니다. 팀 설정에서 변경해 주세요.')
            slack=c.execute("SELECT value FROM settings WHERE key='slack'").fetchone()
            if slack and json.loads(slack[0]).get('connections'):raise ValueError('Slack에 연결된 팀은 팀 설정에서 변경해 주세요.')
            team={**validate_team(rec['team']),'configured':True,'updated':time.time()}
            c.execute("INSERT INTO settings VALUES('workspace',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(team,ensure_ascii=False),))
        row=c.execute("SELECT value FROM settings WHERE key='resource_skills'").fetchone();saved=json.loads(row[0]) if row else {}
        for key in dict.fromkeys(chosen_skills):saved[key]={**saved.get(key,{}),'installed':True}
        c.execute("INSERT INTO settings VALUES('resource_skills',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(saved,ensure_ascii=False),))
        value={'signature':signature,'state':'completed','tasks':rec['tasks'],'engine':rec['engine'],'programs':list(dict.fromkeys(selected)),'skills':list(dict.fromkeys(chosen_skills)),'updated':time.time()}
        c.execute("INSERT INTO settings VALUES('onboarding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(value,ensure_ascii=False),))
    return value
