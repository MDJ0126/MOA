"""MOA-owned Slack CLI onboarding: authorize once, add named bot profiles."""
from organization import display_name
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from private_files import private_directory
from setup_config import manifest,protect,team_settings,save_team,slack_settings,save_slack,read_setting,write_setting

ROOT=Path(__file__).resolve().parent.parent
CLI_URL='https://downloads.slack-edge.com/slack-cli/slack_cli_latest_windows_64-bit.zip'


def clean(text):
    text=re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)','',text)
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
    return text.replace('\r','')


def redact(text):
    return re.sub(r'(?:xox[baprs]-|xapp-|xoxe\.)[^\s"\x1b]+','[보호된 인증 정보]',clean(text))[-1800:]


class Console:
    """ConPTY is required by the official Go CLI on Windows; no window is shown."""
    def __init__(self,argv,cwd,env=None):
        sys.path.insert(0,str(ROOT/'runtime/pythonlibs'))
        from winpty import PtyProcess
        self.process=PtyProcess.spawn(argv,cwd=str(cwd),env=env or dict(os.environ),dimensions=(45,220))
        self.process.fileobj.settimeout(.5)

    def read(self):
        try:return self.process.read(16384)
        except socket.timeout:return ''
        except EOFError:return ''

    def alive(self):return self.process.isalive()
    def exitstatus(self):return self.process.exitstatus
    def close(self):self.process.close(force=True)


class SlackAuto:
    def __init__(self,store):
        self.store=store;self.folder=store.root/'slack-auto';self.lock=threading.RLock();self.stop=threading.Event()
        self.state={'phase':'idle','message':'Slack 인증 후 팀원 이름으로 봇을 추가하세요.','accounts':[],'bots':[]}
        self.busy=False;self.ticket='';self.ticket_until=0;self.process=None;self.pending_add=None
        self.profile_thread=None;self.profile_pending=set()
        self.cli=ROOT/'runtime/slack-cli/bin/slack.exe'

    def snapshot(self):
        with self.lock:
            result=copy.deepcopy(self.state)
            if not result['accounts']:result['accounts']=read_setting(self.store,'slack_auto_accounts',[])
            result['busy']=self.busy
            if self.ticket and time.time()<self.ticket_until:result.update(auth_command='/slackauthticket '+self.ticket,auth_expires=self.ticket_until)
            result['cli_installed']=self.cli.is_file()
            config=slack_settings(self.store)
            result['bots']=[{'agent_id':c['agent_id']} for c in config.get('connections',[]) if c.get('has_bot_token')]
            result['dm_capability']=read_setting(self.store,'slack_dm_capability',{})
            result['profile_sync']=read_setting(self.store,'slack_profile_sync',{})
            if result['dm_capability'].get('state')=='unsupported' and config.get('team_dm'):
                result.update(phase='dm_unsupported',message='Slack 인증과 팀원 봇 등록은 완료됐습니다. Slack이 봇끼리의 1:1 DM을 허용하지 않습니다(cannot_dm_bot). 팀원 간 대화 방식 확인이 필요합니다.')
            if self.pending_add:result['pending_agent_id']=self.pending_add.get('agent_id')
            return result

    def update(self,**values):
        with self.lock:self.state.update(values)

    def prepare(self):
        if os.name!='nt':raise ValueError('자동 봇 연결은 현재 Windows용입니다.')
        if not self.folder.exists():private_directory(self.folder)
        if self.folder.is_symlink() or self.folder.resolve()!=self.folder.absolute():raise ValueError('자동 연결 보호 폴더 경로 오류')
        if not self.cli.is_file():
            self.update(phase='preparing',message='Slack 공식 연결 도구를 준비하고 있습니다.')
            self.cli.parent.mkdir(parents=True,exist_ok=True)
            archive=self.cli.parent.parent/'slack-cli.zip'
            with urllib.request.urlopen(CLI_URL,timeout=30) as response:body=response.read(64*1024*1024+1)
            if len(body)>64*1024*1024:raise ValueError('Slack 연결 도구 크기 제한')
            archive.write_bytes(body)
            with zipfile.ZipFile(archive) as z:
                entry=next(i for i in z.infolist() if i.filename.replace('\\','/').endswith('/slack.exe'))
                if entry.file_size>80*1024*1024:raise ValueError('Slack 실행 파일 크기 제한')
                data=z.read(entry)
                if not data.startswith(b'MZ'):raise ValueError('Slack 실행 파일 형식 오류')
                self.cli.write_bytes(data)

    def arguments(self,*args):return [str(self.cli),*args,'--config-dir',str(self.folder.resolve()),'--skip-update','--no-color','--accessible']

    def run(self,args,cwd=None,timeout=45,ready=None,env=None):
        process=Console(self.arguments(*args),cwd or self.folder,env)
        self.process=process;output='';deadline=time.time()+timeout
        try:
            while time.time()<deadline and not self.stop.is_set():
                output=(output+process.read())[-40000:]
                if ready and ready():return clean(output)
                if not process.alive():
                    output+=process.read()
                    if process.exitstatus() not in (None,0):raise ValueError('Slack 연결 도구: '+redact(output))
                    return clean(output)
            raise ValueError('Slack 자동 설정 응답 시간이 초과되었습니다. 다시 시도하세요.')
        finally:
            process.close();self.process=None

    def dispatch(self,action,value):
        if action not in ('login','challenge','refresh','add'):raise ValueError('자동 연결 작업 오류')
        with self.lock:
            if self.busy:raise ValueError('진행 중인 Slack 설정이 끝날 때까지 잠시 기다려 주세요.')
            self.busy=True
        thread=threading.Thread(target=self.work,args=(action,copy.deepcopy(value)),daemon=True);thread.start()
        return self.snapshot()

    def work(self,action,value):
        try:
            self.prepare()
            if action=='login':
                self.begin_login()
            elif action=='challenge':
                code=value.get('challenge','').strip()
                if not self.ticket or time.time()>self.ticket_until:raise ValueError('인증이 만료되었습니다. Slack 인증을 다시 시작하세요.')
                if not re.fullmatch('[A-Za-z0-9_-]{4,128}',code):raise ValueError('Slack에서 표시된 확인 코드를 입력하세요.')
                output=self.run(['auth','login','--no-prompt','--ticket',self.ticket,'--challenge',code])
                if not self.accounts():raise ValueError('Slack 인증 확인 실패. '+redact(output))
                self.ticket='';self.update(phase='authorized',message='인증되었습니다. 이제 팀원을 선택하고 봇 추가를 누르세요.')
                if self.pending_add:
                    pending=self.pending_add
                    self.add(pending)
                    self.pending_add=None
            elif action=='refresh':
                accounts=self.accounts();self.update(phase='authorized' if accounts else 'idle',message='Slack 인증을 확인했습니다.' if accounts else '먼저 Slack 인증을 진행하세요.')
            else:self.add(value)
        except Exception as e:
            self.update(phase='error',message=redact(str(e)))
        finally:
            with self.lock:self.busy=False

    def begin_login(self):
        self.update(phase='authorizing',message='Slack 일회용 인증을 준비하고 있습니다.')
        output=self.run(['auth','login','--no-prompt'])
        match=re.search(r'/slackauthticket\s+([A-Za-z0-9_\-]+)',output)
        if not match:raise ValueError('Slack 인증을 시작하지 못했습니다. '+redact(output))
        self.ticket=match[1];self.ticket_until=time.time()+600
        self.update(phase='awaiting_approval',message='아래 인증 명령을 Slack에 붙여 넣고 승인 후 확인 코드를 입력하세요.'+(' 인증되면 선택한 팀원 봇 추가를 자동으로 이어갑니다.' if self.pending_add else ''))

    def accounts(self):
        output=self.run(['auth','list'],timeout=20)
        pairs=[]
        # Official CLI human-readable output includes a Team ID and User ID per account.
        chunks=re.split(r'(?=\bTeam ID:)',output)
        for chunk in chunks:
            team=re.search(r'\b(T[A-Z0-9]{5,})\b',chunk);user=re.search(r'\b([UW][A-Z0-9]{5,})\b',chunk)
            if team and user:pairs.append({'team_id':team[1],'user_id':user[1]})
        self.update(accounts=pairs)
        write_setting(self.store,'slack_auto_accounts',pairs)
        return pairs

    def add(self,value):
        self.update(active_agent_id=value.get('agent_id'))
        accounts=self.accounts()
        if not accounts:
            self.pending_add=copy.deepcopy(value)
            self.begin_login()
            return
        team_id=value.get('team_id') or accounts[0]['team_id']
        account=next((a for a in accounts if a['team_id']==team_id),None)
        if not account:raise ValueError('인증한 Slack 워크스페이스를 선택하세요.')
        team=team_settings(self.store)
        agent=next((a for a in team['agents'] if a['id']==value.get('agent_id')),None)
        if not agent:raise ValueError('먼저 팀 설정에서 담당 팀원을 추가하세요.')
        name=display_name(agent).strip()
        if not 1<=len(name)<=35:raise ValueError('Slack 표시 이름은 1~35자로 입력하세요.')
        if value.get('image_base64'):
            import team_profiles
            try:picture=base64.b64decode(value['image_base64'],validate=True)
            except (ValueError,TypeError):raise ValueError('프로필 PNG 파일 형식 오류') from None
            team_profiles.validate_png(picture)
        project=self.folder/('person-'+agent['id'])
        if not project.exists():private_directory(project)
        if project.resolve()!=project.absolute():raise ValueError('팀원 프로젝트 경로 오류')
        previous=project/'manifest.json'
        data=json.loads(previous.read_text(encoding='utf-8')) if value.get('_profile_sync') and previous.is_file() else manifest(name)
        data['display_information']['name']=name;data['display_information']['description']=name+' · MOA AI 팀원'
        if not value.get('_profile_sync'):
            data['features']['bot_user']['display_name']=(re.sub(r'[^a-z0-9_-]','',name.lower()) or ('moa_'+agent['id']))[:80]
        data['settings']['is_hosted']=False
        data['oauth_config']['scopes']['bot']=list(dict.fromkeys(data['oauth_config']['scopes']['bot']+['channels:manage','channels:join']))
        (project/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        hooks=project/'.slack';hooks.mkdir(exist_ok=True)
        command=subprocess.list2cmdline([sys.executable,str(ROOT/'src/slack_cli_hook.py'),'hooks'])
        (hooks/'hooks.json').write_text(json.dumps({'hooks':{'get-hooks':command}}),encoding='utf-8')
        (hooks/'config.json').write_text(json.dumps({'manifest':{'source':'local'}}),encoding='utf-8')
        icon=project/'profile.png'
        import team_profiles
        saved_image=team_profiles.image_path(self.store,agent['id'])
        target_fingerprint=hashlib.sha256((name+'\0'+(saved_image.stem if saved_image else 'default')).encode()).hexdigest()
        if saved_image:
            shutil.copyfile(saved_image,icon)
        elif value.get('image_base64'):
            try:picture=base64.b64decode(value['image_base64'],validate=True)
            except (ValueError,TypeError):raise ValueError('프로필 PNG 파일 형식 오류') from None
            if not picture.startswith(b'\x89PNG\r\n\x1a\n') or not 24<=len(picture)<=1024*1024:raise ValueError('프로필은 1MB 이하 PNG 파일을 선택하세요.')
            width,height=struct.unpack('>II',picture[16:24])
            if width!=height or not 512<=width<=2000:raise ValueError('프로필 PNG는 정사각형 512~2000px이어야 합니다.')
            icon.write_bytes(picture)
        elif not icon.exists():shutil.copyfile(ROOT/'docs/assets/moa-logo.png',icon)
        registration=project/'registration.bin'
        # This file only contains encrypted credentials written by our start hook.
        if registration.exists():registration.unlink()
        self.update(phase='creating',message=name+' 팀원 봇을 생성·설치하고 있습니다. 잠시 기다려 주세요.')
        env={**os.environ,'SLACK_CLI_APP_ICON_PATH':str(icon.resolve())}
        output=self.run(['run','--team',team_id,'--app','local','--manifest-source','local','--no-activity','--force'],
                        project,timeout=180,ready=registration.exists,env=env)
        if not registration.exists():
            if 'ratelimited' in output or 'too quickly' in output:raise ValueError('Slack이 봇 생성 간격을 제한했습니다. 잠시 기다린 뒤 같은 팀원에서 다시 시도하세요.')
            raise ValueError('봇 자동 등록을 완료하지 못했습니다. '+redact(output))
        credentials=json.loads(protect(base64.b64decode(registration.read_bytes()),True))
        registration.unlink()
        if not value.get('_profile_sync'):self.register(agent,name,account,credentials)
        else:
            current=slack_settings(self.store,secrets=True)
            existing=next((c for c in current['connections'] if c['agent_id']==agent['id']),None)
            if not existing:raise ValueError('연결된 팀원 봇을 찾을 수 없습니다.')
            if any(existing.get(k)!=credentials[k] for k in ('bot_token','app_token')):
                existing.update(credentials);save_slack(self.store,{**current,'expected_updated':current.get('updated',0)})
        verified=self.normalize_remote_profile(agent,account,project)
        self.profile_state(agent['id'],state='synced',fingerprint=target_fingerprint,message='팀원 프로필을 Slack에 반영했습니다.',checked_at=time.time(),verified_name=verified['name'])

    def normalize_remote_profile(self,agent,account,project):
        """CLI local-run suffixes are not the teammate's actual display name."""
        from slack_service import WebClient
        path=self.folder/'credentials.json'
        if path.is_symlink() or path.resolve().parent!=self.folder.resolve():raise ValueError('Slack 인증 파일 경로 오류')
        credentials=json.loads(path.read_text(encoding='utf-8')).get(account['team_id'],{})
        token=credentials.get('token','')
        if not token.startswith('xoxe.'):raise ValueError('Slack 설정 인증을 다시 확인해 주세요.')
        linked=json.loads((project/'.slack/apps.dev.json').read_text(encoding='utf-8')).get(account['team_id'],{})
        app_id=linked.get('app_id','')
        if not re.fullmatch('A[A-Z0-9]{5,}',app_id):raise ValueError('연결된 Slack 앱 ID를 확인하지 못했습니다.')
        client=WebClient(token=token,timeout=15)
        remote=client.apps_manifest_export(app_id=app_id)['manifest']
        if isinstance(remote,str):remote=json.loads(remote)
        # The manifest handle and workspace bot display name are different.
        # Preserve the handle. Rewriting it is not a display-name verification.
        target=display_name(agent)
        if remote['display_information']['name']==target:return {'name':target}
        original_bot=copy.deepcopy(remote.get('features',{}).get('bot_user'))
        remote['display_information']['name']=target
        result=client.apps_manifest_update(app_id=app_id,manifest=json.dumps(remote,ensure_ascii=False))
        if result.get('permissions_updated'):raise ValueError('Slack이 권한 재승인을 요청했습니다. 기존 연결 상태를 확인해 주세요.')
        checked=client.apps_manifest_export(app_id=app_id)['manifest']
        if isinstance(checked,str):checked=json.loads(checked)
        if checked['display_information']['name']!=target or checked.get('features',{}).get('bot_user')!=original_bot:raise ValueError('Slack에서 앱 이름 반영 또는 기존 봇 설정 보존을 확인하지 못했습니다.')
        return {'name':display_name(agent)}

    def profile_state(self,aid,**value):
        with self.lock:
            states=read_setting(self.store,'slack_profile_sync',{});states[aid]={**states.get(aid,{}),**value};write_setting(self.store,'slack_profile_sync',states)

    def request_profiles(self,ids=None):
        import team_profiles
        connections={c['agent_id'] for c in slack_settings(self.store)['connections'] if c.get('has_bot_token')}
        states=read_setting(self.store,'slack_profile_sync',{})
        with self.lock:
            for a in team_settings(self.store)['agents']:
                if a['id'] not in connections or ids is not None and a['id'] not in ids:continue
                fingerprint=team_profiles.fingerprint(self.store,a)
                if states.get(a['id'],{}).get('state')=='synced' and states[a['id']].get('fingerprint')==fingerprint:continue
                self.profile_pending.add(a['id']);self.profile_state(a['id'],state='pending',message='Slack 프로필 동기화 대기 중')
            if self.profile_pending and not (self.profile_thread and self.profile_thread.is_alive()):
                self.profile_thread=threading.Thread(target=self.profile_loop,daemon=True);self.profile_thread.start()

    def profile_loop(self):
        while not self.stop.is_set():
            with self.lock:
                if not self.profile_pending:return
                aid=None
                if not self.busy:
                    aid=self.profile_pending.pop();self.busy=True
            if aid is None:self.stop.wait(.5);continue
            try:
                project=self.folder/('person-'+aid)
                if not (project/'.slack/apps.dev.json').is_file():raise ValueError('자동 연결로 만든 봇이 아닙니다. 기존 연결을 확인해 주세요.')
                self.profile_state(aid,state='syncing',message='Slack에 이름·프로필 사진 반영 중')
                from slack_service import WebClient
                current=next(c for c in slack_settings(self.store,secrets=True)['connections'] if c['agent_id']==aid)
                team_id=WebClient(token=current['bot_token'],timeout=10).auth_test()['team_id']
                self.add({'agent_id':aid,'_profile_sync':True,'team_id':team_id})
            except Exception as error:self.profile_state(aid,state='failed',message=redact(str(error)),checked_at=time.time())
            finally:
                with self.lock:self.busy=False

    def register(self,agent,name,account,credentials):
        from slack_service import WebClient,test_connection
        client=WebClient(token=credentials['bot_token'],timeout=15)
        identity=client.auth_test()
        if identity.get('team_id')!=account['team_id']:raise ValueError('인증한 워크스페이스와 생성된 봇이 다릅니다.')
        current=slack_settings(self.store)
        channel=current.get('channel')
        if not channel:
            try:channel=client.conversations_create(name='moa-team')['channel']['id']
            except Exception as e:
                if getattr(e,'response',{}).get('error')!='name_taken':raise
                cursor=None
                for _ in range(25):
                    listing=client.conversations_list(types='public_channel',exclude_archived=True,limit=200,cursor=cursor)
                    found=next((x for x in listing.get('channels',[]) if x.get('name')=='moa-team'),None)
                    if found:channel=found['id'];break
                    cursor=listing.get('response_metadata',{}).get('next_cursor')
                    if not cursor:break
        if not channel:raise ValueError('업무 채널을 확인하지 못했습니다. 채널을 선택하세요.')
        if channel.startswith('C'):client.conversations_join(channel=channel)
        cid='person-'+agent['id']
        connections=[{k:v for k,v in c.items() if k in ('id','agent_id')} for c in current.get('connections',[]) if c['agent_id'] not in (agent['id'],'shared')]
        connections.append({'id':cid,'agent_id':agent['id'],**credentials})
        ready={a['id'] for a in team_settings(self.store)['agents']}.issubset({c['agent_id'] for c in connections})
        value={**current,'expected_updated':current.get('updated',0),'enabled':ready,'receive':True,'notify':True,
               'collaboration':True,'team_dm':False,'channel_receipts':True,'channel':channel,'allowed_users':list(dict.fromkeys(current.get('allowed_users',[])+[account['user_id']])),'connections':connections}
        # Save credentials before checking DM permissions so a failed check never
        # forces the owner to recover or copy tokens manually.
        saved=save_slack(self.store,{**value,'enabled':False})
        if ready:
            value={**value,'expected_updated':saved['updated']}
            test_connection(self.store,value)
            save_slack(self.store,value)
        self.update(phase='connected' if ready else 'partial',message=name+' 봇 연결을 저장했습니다.'+(' 팀원 채널 대화·개별 DM·VM 작업을 시작할 수 있습니다.' if ready else ' 나머지 팀원도 봇 추가를 눌러 주세요.'),
                    bots=[{'agent_id':c['agent_id']} for c in connections])

    def close(self):
        self.stop.set()
