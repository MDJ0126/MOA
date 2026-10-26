"""One local profile per teammate; Slack is a synchronized destination."""
import base64
import hashlib
from pathlib import Path
import re
import struct
import time
import zlib
import json
import secrets
from organization import display_name
from setup_config import read_setting,write_setting,team_settings

def public(store):
    saved=read_setting(store,'agent_profiles',{})
    return {a['id']:{**saved.get(a['id'],{}),'image_hash':saved.get(a['id'],{}).get('image_hash','default'),'url':f'/api/team/profile?id={a["id"]}&v={saved.get(a["id"],{}).get("image_hash","default")}','updated':saved.get(a['id'],{}).get('updated',0)} for a in team_settings(store)['agents']}

GALLERY=Path(__file__).resolve().parent/'web/profile-candidates'

def candidates(store,agent,gender=None):
    if agent not in {a['id'] for a in team_settings(store)['agents']}:raise ValueError('팀원을 찾을 수 없습니다.')
    profile=public(store)[agent]
    gender=gender or profile.get('gender','')
    if gender not in ('','male','female'):raise ValueError('프로필 성별을 선택하세요.')
    manifest=json.loads((GALLERY/'manifest.json').read_text(encoding='utf-8')) if (GALLERY/'manifest.json').is_file() else []
    return {'agent_id':agent,'gender':gender,'selected':profile.get('candidate_id',''),'candidates':[{**v,'url':'/api/team/profile-candidate?id='+v['id']} for v in manifest if not gender or v['gender']==gender]}

def candidate_path(key):
    if not isinstance(key,str) or not re.fullmatch(r'(male|female)-\d{2}',key):raise ValueError('추천 이미지를 찾을 수 없습니다.')
    manifest=json.loads((GALLERY/'manifest.json').read_text(encoding='utf-8'))
    if key not in {v['id'] for v in manifest}:raise ValueError('추천 이미지를 찾을 수 없습니다.')
    path=GALLERY/(key+'.png')
    if path.is_symlink() or not path.is_file():raise ValueError('추천 이미지 파일을 확인하세요.')
    return path

def image_path(store,agent):
    value=read_setting(store,'agent_profiles',{}).get(agent,{})
    key=value.get('image_hash','')
    if not re.fullmatch('[a-f0-9]{64}',key):return None
    folder=store.root/'profiles';path=folder/(key+'.png')
    if folder.is_symlink() or path.is_symlink() or not path.is_file() or path.resolve().parent!=folder.resolve() or path.stat().st_nlink!=1:raise ValueError('프로필 이미지 경로를 확인하세요.')
    return path

def validate_png(body):
    if not 32<=len(body)<=1024*1024 or not body.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('프로필은 1MB 이하 PNG로 저장하세요.')
    width,height=struct.unpack('>II',body[16:24])
    if width!=height or not 512<=width<=2000:raise ValueError('프로필 사진은 정사각형 512~2000px이어야 합니다.')
    position=8;data=bytearray();ended=False;header=False
    while position+12<=len(body):
        size=struct.unpack('>I',body[position:position+4])[0];kind=body[position+4:position+8];end=position+8+size
        if size>1024*1024 or end+4>len(body):raise ValueError('프로필 PNG 파일이 손상되었습니다.')
        chunk=body[position+8:end]
        if zlib.crc32(kind+chunk)&0xffffffff!=struct.unpack('>I',body[end:end+4])[0]:raise ValueError('프로필 PNG 무결성 오류')
        if not header:
            if kind!=b'IHDR' or size!=13:raise ValueError('프로필 PNG 헤더 오류')
            header=True
        if kind==b'IDAT':data.extend(chunk)
        if kind==b'IEND':ended=True;break
        position=end+4
    if not ended or not data:raise ValueError('완전한 PNG 사진을 선택하세요.')
    decoder=zlib.decompressobj();pixels=decoder.decompress(bytes(data),width*height*8+height+1)
    if not decoder.eof or decoder.unconsumed_tail or not pixels:raise ValueError('프로필 PNG 픽셀 데이터 오류')
    return body

def save(store,value):
    agent=value.get('agent_id')
    if agent not in {a['id'] for a in team_settings(store)['agents']}:raise ValueError('팀원을 찾을 수 없습니다.')
    candidate=value.get('candidate_id','');gender=value.get('gender')
    if gender is not None and gender not in ('male','female'):raise ValueError('프로필 성별을 선택하세요.')
    if value.get('random') is True:
        options=candidates(store,agent,gender)['candidates']
        if not gender:raise ValueError('랜덤 적용 전에 성별을 선택하세요.')
        if not options:raise ValueError('추천 이미지가 아직 준비되지 않았습니다.')
        candidate=secrets.choice(options)['id']
    if candidate:
        if gender and not candidate.startswith(gender+'-'):raise ValueError('선택한 성별에 맞는 이미지를 선택하세요.')
        body=candidate_path(candidate).read_bytes();gender=candidate.rsplit('-',1)[0]
    else:
      try:body=base64.b64decode(value.get('image_base64',''),validate=True)
      except (ValueError,TypeError):raise ValueError('프로필 사진 형식 오류') from None
    validate_png(body);key=hashlib.sha256(body).hexdigest();folder=store.root/'profiles'
    folder.mkdir(exist_ok=True)
    if folder.is_symlink() or folder.resolve()!=store.root.resolve()/'profiles':raise ValueError('프로필 저장 경로 오류')
    path=folder/(key+'.png')
    if path.is_symlink():raise ValueError('프로필 링크 파일은 사용할 수 없습니다.')
    if not path.exists():
        with path.open('xb') as stream:stream.write(body)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT value FROM settings WHERE key='agent_profiles'").fetchone()
        import json
        profiles=json.loads(row[0]) if row else {};previous=profiles.get(agent,{})
        if value.get('_only_missing') and previous.get('image_hash'):return public(store)
        profiles[agent]={'image_hash':key,'updated':time.time(),'gender':gender or previous.get('gender',''),'candidate_id':candidate}
        c.execute("INSERT INTO settings(key,value) VALUES('agent_profiles',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(profiles),))
    return public(store)

def ensure_defaults(store):
    """Assign a persistent gallery portrait only to teammates without a photo."""
    team=team_settings(store)
    if team.get('recovery_required'):return []
    profiles=read_setting(store,'agent_profiles',{});changed=[]
    for agent in team['agents']:
        previous=profiles.get(agent['id'],{})
        if previous.get('image_hash') and image_path(store,agent['id']):continue
        gender=agent.get('gender') or previous.get('gender')
        # Unspecified means a random visual style, never infer gender from names.
        if gender not in ('male','female'):gender=secrets.choice(('male','female'))
        save(store,{'agent_id':agent['id'],'gender':gender,'random':True,'_only_missing':True})
        changed.append(agent['id'])
    return changed


def fingerprint(store,agent):
    value=public(store).get(agent['id'],{})
    return hashlib.sha256((display_name(agent)+'\0'+value.get('image_hash','default')).encode()).hexdigest()
