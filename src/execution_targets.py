"""User-visible execution identity and explicit installation capabilities."""
import environment_profiles
from setup_config import read_setting

def snapshot(store):
 profile=environment_profiles.settings(store);host=environment_profiles.identity()
 selected_os=host['os'] if profile['target_os']=='auto' else profile['target_os']
 config=read_setting(store,'vm',{});actual=None
 try:
  from vm_runtime import VM
  vm=VM(store).status();actual={'os':vm.get('guest_os'),'state':vm['state'],'name':'Windows VM' if vm.get('guest_os')=='windows' else 'Linux VM'} if vm.get('guest_os') else None
 except (ValueError,OSError):pass
 probe=read_setting(store,'windows_guest_probe',{})
 active=[]
 with store.connect() as c:
  for row in c.execute("SELECT id,execution_plan FROM tasks WHERE status='running'"):
   try:env=__import__('json').loads(row['execution_plan']).get('environment',{})
   except (ValueError,TypeError):continue
   active.append({'task_id':row['id'],'location':'shared_vm' if env.get('kind')=='vm' else 'host','os':env.get('config',{}).get('guest_os','linux') if env.get('kind')=='vm' else host['os']})
 location=profile['location'];label={'host':'현재 PC','shared_vm':'VM','remote':'원격 PC · 미지원'}[location]
 return {'selected':{'location':location,'os':selected_os,'label':label+' · '+str(selected_os)},'host':host,'vm':actual,'active':active,'windows_probe':probe,'configured_vm_os':config.get('guest_os','linux'),'note':'선택 환경은 다음 업무에 적용됩니다. 가동 중 VM과 실제 업무 위치를 별도로 표시합니다.'}

def support(program,location,os_name):
 if location=='remote':return {'supported':False,'label':'설치 미지원','reason':'원격 실행기는 연결되지 않았습니다.'}
 if location=='host' and os_name!='windows':return {'supported':False,'label':'자동 설치 미지원','reason':'현재 PC 자동 설치는 Windows WinGet 경로만 지원합니다.'}
 if location=='shared_vm' and os_name=='linux':
  ok=bool(program.get('guest'));return {'supported':ok,'label':'VM 설치 지원' if ok else '설치 미지원','reason':'번들 Tiny Core 확장 패키지' if ok else '이 Linux VM에 등록된 호환 패키지가 없습니다.'}
 if os_name=='windows':
  manual=program.get('manual') or not program.get('package')
  return {'supported':True,'automatic':not manual,'label':'직접 인증·설치 필요' if manual else '설치 지원','reason':'공식 설치 화면의 로그인·약관·라이선스 확인이 필요합니다.' if manual else 'Windows WinGet · 설치 관리자와 네트워크가 필요합니다.'}
 return {'supported':False,'label':'설치 미지원','reason':'지원되는 설치 실행기가 없습니다.'}
