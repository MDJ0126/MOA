"""Host-aware execution preferences. Saving a preference never provisions a VM."""
import math
import platform
from setup_config import hardware,read_setting,checked_write_setting

DEFAULT={'target_os':'auto','location':'shared_vm','budget_percent':50}
TARGETS=('auto','windows','linux','macos')
LOCATIONS=('host','shared_vm','remote')

class UnavailableEnvironment(ValueError):pass

def identity():
    return {'os':{'Windows':'windows','Linux':'linux','Darwin':'macos'}.get(platform.system()),'architecture':platform.machine()}

def resolve(value,current=None):
    requested=validate(value);current=identity() if current is None else current
    target=current['os'] if requested['target_os']=='auto' else requested['target_os']
    if requested['location']!='host':
        raise UnavailableEnvironment('선택한 VM·원격 실행 환경은 아직 연결되지 않았습니다. 환경 설정에서 현재 PC를 선택하거나 연결 구현 후 다시 요청하세요.')
    if target is None or target!=current['os']:
        raise UnavailableEnvironment('개발 대상 OS와 현재 PC가 다릅니다. 해당 OS의 실행 환경을 연결해야 하며 다른 OS에서 대신 검증하지 않습니다.')
    return {'version':1,'requested':requested,'target_os':target,'runner':'host','architecture':current['architecture']}

def check_contract(value,kind,current=None):
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1:raise ValueError('실행 환경 계약 형식 오류')
    if kind!='local':raise ValueError('호스트 실행 계약과 작업 환경이 다릅니다.')
    expected=resolve(value.get('requested'),current)
    if value!=expected:raise UnavailableEnvironment('접수된 OS·아키텍처와 현재 실행 환경이 달라 작업을 보류했습니다. 원래 환경에서 재개하거나 새 작업을 등록하세요.')
    return expected

def settings(store):
    return read_setting(store,'execution_profile',DEFAULT)

def recommend(value,host=None):
    value=validate(value)
    host=hardware() if host is None else host
    native={'Windows':'windows','Linux':'linux','Darwin':'macos'}.get(host.get('os'))
    target=native if value['target_os']=='auto' else value['target_os']
    location=value['location'];percent=value['budget_percent']
    total=host.get('ram_gib');free=host.get('available_ram_gib');cores=host.get('logical_cpus')
    known=lambda n:isinstance(n,(float,int)) and not isinstance(n,bool) and math.isfinite(n) and n>0
    cpu=max(1,min(4,int(cores*percent/100))) if known(cores) else None
    usage=host.get('cpu_used_percent')
    if cpu is not None and isinstance(usage,(float,int)) and not isinstance(usage,bool) and math.isfinite(usage) and 0<=usage<=100:
        cpu=min(cpu,max(0,int(cores*(1-usage/100))))
    memory=None
    if known(total) and known(free):
        # Keep 6 GiB for the shared model/host, and 2 GiB of current free memory.
        ceiling=max(0,min(total*percent/100,total-6,free-2))
        memory=int(ceiling*1024//512)*512
    minimum={'windows':4096,'linux':2048,'macos':8192}.get(target,2048)
    presets=[{'id':'light','memory_mb':minimum,'cpus':2 if target=='windows' else 1},
             {'id':'balanced','memory_mb':max(minimum,6144 if target=='windows' else 4096),'cpus':2},
             {'id':'development','memory_mb':max(minimum,8192),'cpus':4}]
    for preset in presets:
        preset['fits_now']=None if memory is None or cpu is None or location!='shared_vm' else preset['memory_mb']<=memory and preset['cpus']<=cpu
    fitting=[p for p in presets if p['fits_now'] is True]
    selected=fitting[-1]['id'] if fitting else None
    if location=='remote':
        memory=cpu=None;note='원격 장비 사양 미확인 · 연결 후 해당 장비에서 측정해야 합니다.'
    elif location=='host':
        memory=cpu=None;note='호스트 프로세스의 자원 상한은 적용하지 않습니다. 대상 OS가 현재 PC와 같아야 실제 검증할 수 있습니다.'
    elif memory is None:note='여유 메모리 미확인 · 자동 자원 추천을 보류합니다.'
    elif not fitting:note='현재 여유 자원으로 권장 VM 구성을 충족하지 못합니다. 다른 앱을 정리하거나 원격 장비를 사용하세요.'
    else:note='현재 여유 RAM과 요청 점유 비율을 반영한 보수적 시작값입니다. 실행 중 자원을 자동 변경하지 않습니다.'
    if target=='macos':note+=' macOS 실행은 Mac 장비 연결을 기준으로 준비하세요.'
    return {'host':host,'target_os':target,'location':location,'presets':presets,'recommended':selected,
            'memory_budget_mb':memory,'cpu_budget':cpu,'note':note,'connected':False,
            'runtime_note':'새 작업에서 저장한 실행 환경을 선택하면 현재 PC와 대상 OS를 확인해 실행합니다. VM·원격 연결은 아직 미구현이며 미연결 대상을 대신 실행하지 않습니다.'}

def validate(value):
    if not isinstance(value,dict):raise ValueError('실행 환경 설정 객체가 필요합니다.')
    clean={key:value.get(key,default) for key,default in DEFAULT.items()}
    if clean['target_os'] not in TARGETS or clean['location'] not in LOCATIONS:raise ValueError('대상 OS와 실행 위치를 확인하세요.')
    if type(clean['budget_percent']) is not int or clean['budget_percent'] not in (25,50,75):raise ValueError('자원 점유 목표는 25·50·75% 중 선택하세요.')
    return clean

def save(store,value):
    clean=validate(value)
    checked_write_setting(store,'execution_profile',clean,value)
    return settings(store)
