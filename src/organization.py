"""Canonical organization metadata, reporting lines and polite teammate titles."""
import json
RANKS=('인턴','사원','주임','대리','과장','차장','부장','팀장','실장','본부장','대표')

def display_name(agent):
    title=' '.join(v for v in (agent['name'],agent.get('rank','')) if v)
    return title+(' ('+agent['job_title']+')' if agent.get('job_title') else '')

def honorific(agent):
    return ' '.join(v for v in (agent['name'],agent.get('rank','')) if v)+'님'

def validate(agents):
    lookup={a['id']:a for a in agents}
    for agent in agents:
        if len(display_name(agent))>35:raise ValueError('Slack 표시 이름은 이름·직급·담당 업무를 합쳐 35자 이내로 설정하세요.')
        manager=agent.get('manager_id','')
        if manager and manager not in lookup:raise ValueError('보고 대상 팀원을 찾을 수 없습니다.')
    for agent in agents:
        manager=agent.get('manager_id','')
        seen={agent['id']}
        while manager:
            if manager in seen:raise ValueError('보고 관계는 자기 자신이나 순환 관계로 설정할 수 없습니다.')
            seen.add(manager);manager=lookup[manager].get('manager_id','')

def instruction(agents):
    roster=[{'id':a['id'],'name':a['name'],'rank':a.get('rank',''),'job_title':a.get('job_title',''),'display_name':display_name(a),'address_as':honorific(a),'reports_to':a.get('manager_id',''),'capabilities':a.get('capabilities',[]),'specialty':a.get('specialty','')[:150],'work_kinds':a.get('work_kinds',[]),'responsibilities':a.get('responsibilities','')[:400],'boundaries':a.get('boundaries','')[:350]} for a in agents if isinstance(a,dict) and a.get('id') and a.get('name')]
    return '조직 및 호칭 규칙: 모든 팀원에게 직급의 높고 낮음과 무관하게 정중한 한국어 존댓말을 사용합니다. 상대를 부를 때 이름과 직급에 님을 붙입니다(예: 최서연 대리님). 직급이 없으면 이름+님을 사용합니다. 반말, 이름만 부르기, 하대는 하지 않습니다. 담당 업무·담당 가능한 업무 종류·업무 범위를 존중합니다. 담당하지 않는 일은 적합한 팀원에게 요청하고, 담당자를 찾을 수 없으면 리더가 자신의 범위 안에서 직접 처리합니다. 담당 업무와 실제 실행 역할을 구분하고, 보고 관계를 존중하되 권한이나 도구 접근은 기존 실행 정책을 따릅니다.\n조직 명부: '+json.dumps(roster,ensure_ascii=False)

def snapshot(agents):
    return {'ranks':list(RANKS),'agents':[{**a,'display_name':display_name(a),'honorific':honorific(a)} for a in agents],'roots':[a['id'] for a in agents if not a.get('manager_id')]}
