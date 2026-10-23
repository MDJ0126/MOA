"""User-facing team presets, frozen task assignments, and local setup storage."""
import copy
import base64
import ctypes
import json
import os
from pathlib import Path
import platform
import subprocess
import re
import threading
import time

CAPABILITIES={'planner':'계획','developer':'개발','reviewer':'검증','reporter':'보고'}
DEFAULT_AGENTS=[{'id':key,'name':label+' 담당','capabilities':[key],'instructions':''} for key,label in CAPABILITIES.items()]
PRESETS=[
 {'id':'compact','name':'혼자서 가볍게','count':1,'tag':'최소 구성','description':'한 에이전트가 구현과 검증을 순서대로 맡습니다. 보고서는 자동 정리합니다.',
  'pipeline':['developer','reviewer'],'agents':[{'id':'maker','name':'메이커','capabilities':list(CAPABILITIES),'instructions':''}]},
 {'id':'light','name':'작은 협업 팀','count':2,'tag':'추천 시작점','description':'만드는 담당과 확인하는 담당을 나눕니다. 계획·구현·검증 후 자동 보고합니다.',
  'pipeline':['planner','developer','reviewer'],'agents':[{'id':'builder','name':'빌더','capabilities':['planner','developer'],'instructions':''},{'id':'checker','name':'체커','capabilities':['reviewer','reporter'],'instructions':''}]},
 {'id':'standard','name':'역할마다 전문적으로','count':4,'tag':'전체 흐름','description':'계획·개발·검증·보고를 각각 맡깁니다. 네 역할이 차례로 실행됩니다.',
  'pipeline':list(CAPABILITIES),'agents':DEFAULT_AGENTS},
]
LOCK=threading.RLock()

class SettingsConflict(ValueError):pass

def check_revision(current,value):
    expected=value.get('expected_updated')
    if expected is not None and expected!=current.get('updated',0):
        raise SettingsConflict('다른 창에서 설정이 변경되었습니다. 입력을 보관한 뒤 최신 설정을 불러와 다시 적용하세요.')

def checked_write_setting(store,key,clean,value):
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        current=json.loads(row[0]) if row else {}
        check_revision(current,value)
        clean['updated']=max(clean.get('updated',time.time()),current.get('updated',0)+.000001)
        if key=='workspace':
            row=c.execute("SELECT value FROM settings WHERE key='slack'").fetchone()
            slack=json.loads(row[0]) if row else {}
            available={agent['id'] for agent in clean['agents']}
            if slack.get('enabled') and any(conn['agent_id']!='shared' and conn['agent_id'] not in available for conn in slack.get('connections',[])):
                raise ValueError('삭제할 팀원에 Slack 봇이 연결되어 있습니다. 먼저 연결을 변경하세요.')
        c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(clean,ensure_ascii=False)))

def read_setting(store,key,default):
    with store.connect() as c:row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else copy.deepcopy(default)

def write_setting(store,key,value):
    with store.connect() as c:c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value,ensure_ascii=False)))

def team_settings(store):
    return read_setting(store,'workspace',{'configured':False,'preset':'standard','agents':DEFAULT_AGENTS,'pipeline':list(CAPABILITIES),'max_revisions':2})

def validate_team(value):
    if not isinstance(value,dict):raise ValueError('팀 설정 객체가 필요합니다.')
    agents=value.get('agents');pipeline=value.get('pipeline',list(CAPABILITIES))
    if not isinstance(agents,list) or not 1<=len(agents)<=8:raise ValueError('에이전트는 1~8명으로 설정하세요.')
    if not isinstance(pipeline,list) or any(not isinstance(r,str) or r not in CAPABILITIES for r in pipeline) or len(set(pipeline))!=len(pipeline):raise ValueError('실행 단계가 올바르지 않습니다.')
    if 'developer' not in pipeline or 'reviewer' not in pipeline or pipeline.index('developer')>pipeline.index('reviewer'):raise ValueError('개발 다음에 검증 단계가 필요합니다.')
    if 'planner' in pipeline and pipeline[0]!='planner':raise ValueError('계획은 첫 단계여야 합니다.')
    if 'reporter' in pipeline and pipeline[-1]!='reporter':raise ValueError('보고는 마지막 단계여야 합니다.')
    ids=set();clean=[]
    for agent in agents:
        if not isinstance(agent,dict) or any(not isinstance(agent.get(k,''),str) for k in ('id','name','instructions')):raise ValueError('팀원 이름과 지침은 문자열이어야 합니다.')
        aid=agent.get('id','');name=agent.get('name','').strip();roles=agent.get('capabilities',[]);instructions=agent.get('instructions','').strip()
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',aid) or aid in ids or aid in ('system','user'):raise ValueError('에이전트 ID는 중복되지 않는 영문 소문자로 설정하세요.')
        if not 1<=len(name)<=30 or not isinstance(roles,list) or not roles or any(not isinstance(r,str) or r not in CAPABILITIES for r in roles):raise ValueError('이름과 역할을 확인하세요.')
        if len(instructions)>2000:raise ValueError('추가 지침은 2,000자 이내로 입력하세요.')
        ids.add(aid);clean.append({'id':aid,'name':name,'capabilities':list(dict.fromkeys(roles)),'instructions':instructions})
    if any(not any(role in a['capabilities'] for a in clean) for role in pipeline):raise ValueError('각 실행 단계에 담당자가 한 명 이상 필요합니다.')
    revisions=int(value.get('max_revisions',2))
    if not 0<=revisions<=5:raise ValueError('수정 횟수는 0~5회로 설정하세요.')
    return {'configured':True,'preset':value.get('preset',value.get('id','custom')),'agents':clean,'pipeline':pipeline,'max_revisions':revisions,'updated':time.time()}

def save_team(store,value):
    clean=validate_team(value)
    slack=read_setting(store,'slack',{})
    if slack.get('enabled'):
        available={a['id'] for a in clean['agents']}
        if any(c['agent_id']!='shared' and c['agent_id'] not in available for c in slack.get('connections',[])):
            raise ValueError('삭제할 팀원에 Slack 봇이 연결되어 있습니다. 먼저 그 봇의 담당을 공유 팀 봇으로 변경하세요.')
    checked_write_setting(store,'workspace',clean,value)
    return clean

def make_plan(store,tid):
    setup=team_settings(store)
    assignment={};load={a['id']:0 for a in setup['agents']}
    for role in setup['pipeline']:
        candidates=[a for a in setup['agents'] if role in a['capabilities']]
        if role=='reviewer' and any(a['id']!=assignment.get('developer') for a in candidates):
            candidates=[a for a in candidates if a['id']!=assignment.get('developer')]
        minimum=min(load[a['id']] for a in candidates)
        candidates=[a for a in candidates if load[a['id']]==minimum]
        selected=candidates[int(tid,16)%len(candidates)]['id']
        assignment[role]=selected;load[selected]+=1
    return {'pipeline':setup['pipeline'],'agents':setup['agents'],'assignment':assignment}

def legacy_plan():
    return {'pipeline':list(CAPABILITIES),'agents':copy.deepcopy(DEFAULT_AGENTS),'assignment':{r:r for r in CAPABILITIES}}

_cpu_sample=None

def cpu_usage(system):
    """Non-blocking difference between two host snapshots; first sample unknown."""
    global _cpu_sample
    try:
        if system=='Windows':
            idle=ctypes.c_ulonglong();kernel=ctypes.c_ulonglong();user=ctypes.c_ulonglong()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel),ctypes.byref(user)):return None
            sample=(system,idle.value,kernel.value+user.value)
        elif system=='Linux':
            fields=Path('/proc/stat').read_text().splitlines()[0].split()
            if fields[0]!='cpu':return None
            values=list(map(int,fields[1:9]))
            if len(values)<5 or any(value<0 for value in values):return None
            sample=(system,values[3]+values[4],sum(values))
        else:return None
    except (OSError,ValueError,IndexError,AttributeError):return None
    with LOCK:
        previous=_cpu_sample;_cpu_sample=sample
    if previous is None or previous[0]!=system or sample[2]<=previous[2] or sample[1]<previous[1]:return None
    if sample[1]-previous[1]>sample[2]-previous[2]:return None
    return round(max(0,min(100,100*(1-(sample[1]-previous[1])/(sample[2]-previous[2])))),1)

def _memory_reading(total,available=None):
    if type(total) is not int or total<=0:return {}
    result={'ram_gib':round(total/2**30,2)}
    if type(available) is int and 0<=available<=total:result['available_ram_gib']=round(available/2**30,2)
    return result

def hardware():
    result={'os':platform.system(),'logical_cpus':os.cpu_count(),'ram_gib':None,'available_ram_gib':None}
    if result['os']=='Windows':
        class M(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','p1','p2','v1','v2','ex')]
        m=M();m.length=ctypes.sizeof(m)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):result.update(_memory_reading(m.total,m.available))
        except (OSError,AttributeError):pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:name=winreg.QueryValueEx(key,'ProcessorNameString')[0]
            if isinstance(name,str) and name.strip():result['cpu']=name.strip()
        except (OSError,ImportError):pass
    elif platform.system()=='Linux':
        try:
            values={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:'))}
            result.update(_memory_reading(values['MemTotal'],values.get('MemAvailable')))
        except (OSError,ValueError,KeyError,IndexError,OverflowError):pass
    elif platform.system()=='Darwin':
        try:
            query=subprocess.run(['/usr/sbin/sysctl','-n','hw.memsize'],capture_output=True,text=True,timeout=2,check=True)
            result.update(_memory_reading(int(query.stdout.strip())))
        except (OSError,ValueError,OverflowError,subprocess.SubprocessError):pass
        # Reclaimable macOS memory is not equivalent to free pages; leave unknown.
    total=result['ram_gib'];free=result['available_ram_gib']
    result['memory_used_percent']=round(max(0,min(100,100*(1-free/total))),1) if total and free is not None else None
    result['architecture']=platform.machine()
    result['cpu_used_percent']=cpu_usage(result['os'])
    ram=result['ram_gib']
    result['recommended_preset']='compact' if ram is not None and ram<12 else 'light' if ram is None or ram<24 else 'standard'
    result['recommendation_note']='총 RAM에 따른 시작 구성입니다. 실제 모델 속도와 메모리는 작업에 따라 달라집니다. 모든 프리셋은 순차 실행합니다.'
    return result

class Blob(ctypes.Structure):
    _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]

def protect(raw,decode=False):
    if os.name!='nt':raise ValueError('현재 토큰 저장은 Windows 사용자 암호화(DPAPI)를 지원합니다.')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));output=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decode else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):raise OSError('사용자 암호화 저장소에 접근하지 못했습니다.')
    try:return ctypes.string_at(output.data,output.size)
    finally:ctypes.windll.kernel32.LocalFree(output.data)

def secret_values(store):
    encoded=read_setting(store,'slack_secret_blob',None)
    path=store.root/'slack.secrets'
    raw=base64.b64decode(encoded,validate=True) if encoded is not None else path.read_bytes() if path.exists() else None
    values=json.loads(protect(raw,True)) if raw is not None else {}
    if not isinstance(values,dict) or any(not isinstance(key,str) or not isinstance(value,dict) or any(not isinstance(token,str) for token in value.values()) for key,value in values.items()):raise ValueError('암호화 토큰 형식 오류')
    return values

def slack_settings(store,secrets=False):
    config=read_setting(store,'slack',{'enabled':False,'connections':[],'allowed_users':[],'channel':'','receive':True,'notify':True,'updated':0})
    try:values=secret_values(store)
    except (OSError,ValueError):
        if secrets:raise
        values={};config['secret_error']='저장된 토큰을 이 Windows 계정에서 읽지 못했습니다. 새 토큰으로 교체하거나 원래 계정에서 복구하세요.'
    for connection in config['connections']:
        saved=values.get(connection['id'],{})
        if secrets:connection.update(saved)
        else:
            connection['has_bot_token']=bool(saved.get('bot_token'))
            connection['has_app_token']=bool(saved.get('app_token'))
    return config

def prepare_slack(store,value):
    if not isinstance(value,dict):raise ValueError('Slack 설정 객체가 필요합니다.')
    if any(not isinstance(value.get(k,default),bool) for k,default in [('enabled',False),('receive',True),('notify',True)]):raise ValueError('Slack 수신·전송 설정은 참 또는 거짓이어야 합니다.')
    if not isinstance(value.get('channel',''),str):raise ValueError('채널 ID는 문자열이어야 합니다.')
    if not isinstance(value.get('replace_secrets',False),bool):raise ValueError('토큰 교체 설정 오류')
    old={} if value.get('replace_secrets') else secret_values(store)
    connections=[];values={};agents={a['id'] for a in team_settings(store)['agents']}
    enabled=value.get('enabled',False);receive=value.get('receive',True);notify=value.get('notify',True)
    channel=value.get('channel','').strip()
    users=value.get('allowed_users',[])
    if not isinstance(users,list) or any(not isinstance(u,str) or not re.fullmatch('[UW][A-Z0-9]{5,}',u) for u in users):raise ValueError('Slack 멤버 ID를 확인하세요. U 또는 W로 시작합니다.')
    if channel and not re.fullmatch('[CG][A-Z0-9]{5,}',channel):raise ValueError('채널 ID를 확인하세요. C 또는 G로 시작합니다.')
    source=value.get('connections',[])
    if not isinstance(source,list) or len(source)>9:raise ValueError('Slack 연결은 최대 9개입니다.')
    ids=set()
    for entry in source:
        if not isinstance(entry,dict) or any(not isinstance(entry.get(k,''),str) for k in ('id','bot_token','app_token')) or not isinstance(entry.get('agent_id','shared'),str):raise ValueError('Slack 연결과 토큰 형식을 확인하세요.')
        cid=entry.get('id','');agent=entry.get('agent_id','shared')
        if not re.fullmatch('[a-z][a-z0-9_-]{0,31}',cid) or cid in ids:raise ValueError('Slack 연결 ID 오류')
        if agent!='shared' and agent not in agents:raise ValueError('연결할 에이전트가 없습니다.')
        if any(c['agent_id']==agent for c in connections):raise ValueError('같은 담당자에는 봇 연결을 하나만 설정하세요.')
        ids.add(cid)
        saved={} if entry.get('clear') else copy.deepcopy(old.get(cid,{}))
        for key,prefix in [('bot_token','xoxb-'),('app_token','xapp-')]:
            token=entry.get(key,'').strip()
            if token:
                if not token.startswith(prefix) or len(token)>500:raise ValueError('Slack 토큰 형식을 확인하세요.')
                saved[key]=token
        if enabled and (not saved.get('bot_token') or (receive and not saved.get('app_token'))):raise ValueError('봇 토큰과 App-Level 토큰이 필요합니다.')
        values[cid]=saved;connections.append({'id':cid,'agent_id':agent})
    if enabled and not connections:raise ValueError('Slack 연결을 추가하세요.')
    if enabled and not (receive or notify):raise ValueError('지시 수신 또는 결과 보고를 하나 이상 선택하세요.')
    if enabled and receive and not users:raise ValueError('업무 지시를 허용할 Slack 멤버 ID를 한 명 이상 입력하세요.')
    if enabled and notify and not channel:raise ValueError('보고를 받을 채널 ID를 입력하세요.')
    config={'enabled':bool(enabled),'receive':bool(receive),'notify':bool(notify),'channel':channel,'allowed_users':users,'connections':connections,'updated':time.time()}
    return config,values

def save_slack(store,value):
    with LOCK:
        config,values=prepare_slack(store,value)
        encrypted=protect(json.dumps(values).encode())
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT value FROM settings WHERE key='slack'").fetchone()
            current=json.loads(row[0]) if row else {}
            check_revision(current,value)
            config['updated']=max(config['updated'],current.get('updated',0)+.000001)
            row=c.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()
            agents={a['id'] for a in (json.loads(row[0])['agents'] if row else DEFAULT_AGENTS)}
            if config['enabled'] and any(conn['agent_id']!='shared' and conn['agent_id'] not in agents for conn in config['connections']):raise ValueError('연결할 에이전트가 변경되었습니다. 최신 팀을 확인하세요.')
            for key,item in [('slack',config),('slack_secret_blob',base64.b64encode(encrypted).decode('ascii'))]:
                c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(item,ensure_ascii=False)))
    return slack_settings(store)

def manifest(name='Local Agent Team'):
    return {'display_information':{'name':name[:35],'description':'로컬 에이전트 팀에 업무를 지시하고 결과를 받습니다.'},
            'features':{'bot_user':{'display_name':name[:35],'always_online':False},'app_home':{'messages_tab_enabled':True,'messages_tab_read_only_enabled':False}},
            'oauth_config':{'scopes':{'bot':['app_mentions:read','chat:write','im:history','channels:read']}},
            'settings':{'socket_mode_enabled':True,'event_subscriptions':{'bot_events':['app_mention','message.im']},'org_deploy_enabled':False,'token_rotation_enabled':False}}
