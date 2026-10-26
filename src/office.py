"""Office appearance and authenticated links to each teammate's human DM."""
from office_activities import OFFICE_ACTIVITY_IDS
from setup_config import read_setting,write_setting,team_settings,slack_settings

LIMITS={'hair':4,'skin':4,'shirt':6,'accessory':3}

def appearance(store):
    return read_setting(store,'office_appearance',{})

def save(store,data):
    agent=data.get('agent_id')
    if agent not in {a['id'] for a in team_settings(store)['agents']}:raise ValueError('팀원을 찾을 수 없습니다.')
    value=data.get('appearance')
    if not isinstance(value,dict) or set(value)!=set(LIMITS):raise ValueError('캐릭터 파츠를 모두 선택하세요.')
    if any(type(value[k]) is not int or not 0<=value[k]<limit for k,limit in LIMITS.items()):raise ValueError('캐릭터 파츠 범위를 확인하세요.')
    saved=appearance(store);saved[agent]=value;write_setting(store,'office_appearance',saved)
    return saved

def direct_message(store,data,client_factory=None):
    from slack_service import WebClient,safe_error
    agent=data.get('agent_id')
    if agent not in {a['id'] for a in team_settings(store)['agents']}:raise ValueError('팀원을 찾을 수 없습니다.')
    config=slack_settings(store,secrets=True)
    connection=next((c for c in config.get('connections',[]) if c['agent_id']==agent and c.get('bot_token')),None)
    if not config.get('enabled') or not connection:raise ValueError('이 팀원의 Slack 봇을 먼저 연결해 주세요.')
    users=config.get('allowed_users',[])
    if not users:raise ValueError('팀 설정에서 대화할 Slack 멤버 ID를 등록하세요.')
    user=data.get('user_id') or (users[0] if len(users)==1 else None)
    if user is None:return {'users':users}
    if user not in users:raise ValueError('허용된 Slack 사용자만 대화할 수 있습니다.')
    client=(client_factory or (lambda token:WebClient(token=token,timeout=10)))(connection['bot_token'])
    try:
        identity=client.auth_test();channel=client.conversations_open(users=user)['channel']['id']
        team=identity['team_id']
        if not channel.startswith('D') or not team.startswith('T'):raise ValueError('Slack DM 주소를 확인하지 못했습니다.')
    except ValueError:raise
    except Exception as error:raise ValueError('Slack DM을 열지 못했습니다: '+safe_error(error)) from None
    return {'url':f'https://app.slack.com/client/{team}/{channel}','channel':channel}


def life(store):
    return read_setting(store,'office_life',{'version':1,'agents':[]})


def save_life(store,data):
    import math
    rows=data.get('agents')
    if data.get('version')!=1 or not isinstance(rows,list) or len(rows)>100:raise ValueError('사무실 상태 형식을 확인하세요.')
    ids={a['id'] for a in team_settings(store)['agents']};clean=[]
    for row in rows:
        if not isinstance(row,list) or len(row)!=2:raise ValueError('팀원 상태 형식을 확인하세요.')
        aid,m=row
        if aid not in ids:continue
        if not isinstance(m,dict) or m.get('mode') not in ('desk','walk','coffee','chat','work','activity') or m.get('phase') not in ('rest','go','brew','chat','look','return','sip','stretch','seated','perform'):raise ValueError('사무실 상태를 확인하세요.')
        if m['mode']=='activity' and m.get('activity') not in OFFICE_ACTIVITY_IDS:raise ValueError('사무실 활동을 확인하세요.')
        until=m.get('until')
        if until is not None and (type(until) not in (int,float) or not math.isfinite(until)):raise ValueError('시간을 확인하세요.')
        dest=m.get('dest')
        if dest is not None:
            if not isinstance(dest,dict):raise ValueError('이동 위치를 확인하세요.')
            x=dest.get('x');offset=dest.get('offset',0)
            if dest.get('coffee') is True:dest={'coffee':True}
            elif type(x) in (int,float) and 0<=x<=1 and type(offset) in (int,float) and -100<=offset<=100:dest={'x':x,'offset':offset,**({'y':dest['y']} if type(dest.get('y')) in (int,float) and math.isfinite(dest['y']) and 0<=dest['y']<=1 else {})}
            else:raise ValueError('이동 위치를 확인하세요.')
        item={'mode':m['mode'],'phase':m['phase'],'until':until,'dest':dest,'cup':m.get('cup') is True}
        if m['mode']=='activity':
            item['activity']=m['activity'];item['round']=max(0,min(3,m.get('round',0))) if type(m.get('round',0)) is int else 0
        position=m.get('position')
        if isinstance(position,dict) and all(type(position.get(k)) in (int,float) and math.isfinite(position[k]) and abs(position[k])<=10000 for k in ('x','y')):item['position']={k:position[k] for k in ('x','y')}
        if m.get('lastActivity') in OFFICE_ACTIVITY_IDS:item['lastActivity']=m['lastActivity']
        if m.get('partner') in ids:item['partner']=m['partner']
        if m.get('side') in (0,1):item['side']=m['side']
        clean.append([aid,item])
    value={'version':1,'agents':clean}
    write_setting(store,'office_life',value)
    return value


def company(store,workspace=''):
    import secrets
    saved=read_setting(store,'company_identity',{})
    if not saved.get('seed'):
        saved={'seed':secrets.token_hex(8),'name':''}
        write_setting(store,'company_identity',saved)
    name=saved.get('name') or ((workspace.strip() or 'MOA')+' 컴퍼니')
    import hashlib
    revision=hashlib.sha256((saved['seed']+saved.get('image','')).encode()).hexdigest()[:16]
    return {'name':name,'custom_name':saved.get('name',''),'workspace':workspace,'icon_url':'/api/company/icon?v='+revision,'slack_icon_sync':'manual','slack_icon_note':'Slack 아이콘 자동 변경은 Enterprise 조직 관리자 API가 필요합니다. 아래에서 이미지를 내려받아 Slack 워크스페이스 설정에 적용하세요.'}


def save_company(store,data):
    import base64,io,secrets
    from PIL import Image,ImageOps,UnidentifiedImageError
    saved=read_setting(store,'company_identity',{})
    name=data.get('name',saved.get('name',''))
    if not isinstance(name,str) or len(name.strip())>60:raise ValueError('회사 이름은 60자 이하로 입력하세요.')
    saved={**saved,'seed':saved.get('seed') or secrets.token_hex(8),'name':name.strip()}
    if data.get('randomize') is True:saved['seed']=secrets.token_hex(8);saved.pop('image',None)
    if 'image_base64' in data:
        try:
            raw=base64.b64decode(data['image_base64'],validate=True)
            if len(raw)>1000000:raise ValueError('1MB 이하 이미지를 선택하세요.')
            with Image.open(io.BytesIO(raw)) as image:
                if image.width>4096 or image.height>4096:raise ValueError('이미지는 4096px 이하로 선택하세요.')
                image=ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'),(512,512))
                out=io.BytesIO();image.save(out,format='PNG');saved['image']=base64.b64encode(out.getvalue()).decode()
        except (UnidentifiedImageError,Image.DecompressionBombError,OSError,TypeError,ValueError) as error:raise ValueError('유효한 1MB 이하 이미지를 선택하세요.') from error
    write_setting(store,'company_identity',saved)
    return {'saved':True}


def company_icon(store):
    import base64,io,random
    from PIL import Image,ImageDraw
    company(store)
    saved=read_setting(store,'company_identity',{})
    if saved.get('image'):return base64.b64decode(saved['image'])
    rng=random.Random(saved['seed'])
    palettes=[('#F9E8DB','#D05B3F','#733F35'),('#E7ECDD','#7C9470','#3E5846'),('#E6EDF3','#6F8DAD','#374E70'),('#F2E8DA','#C79456','#705538'),('#ECE5F0','#A086B3','#655273')]
    bg,accent,dark=rng.choice(palettes);image=Image.new('RGB',(512,512),bg);draw=ImageDraw.Draw(image)
    style=rng.randrange(3)
    if style==0:
        for x,y,color in [(108,108,accent),(272,108,dark),(108,272,dark),(272,272,accent)]:draw.rounded_rectangle((x,y,x+132,y+132),radius=rng.randrange(30,60),fill=color)
    elif style==1:
        draw.rounded_rectangle((112,172,400,400),radius=42,fill=accent);draw.polygon([(92,195),(256,78),(420,195)],fill=dark)
        for x in (156,232,308):
            for y in (228,304):draw.rounded_rectangle((x,y,x+44,y+44),radius=10,fill=bg)
    else:
        for x,y,color in [(110,110,accent),(250,110,dark),(110,250,dark),(250,250,accent)]:draw.ellipse((x,y,x+150,y+150),fill=color)
        draw.ellipse((205,205,307,307),fill=bg)
    out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()
