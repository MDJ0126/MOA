"""Editable occupational presets; execution stages remain separate from duties."""
import copy,re
KINDS=('code','image','research','design','general')
LABELS={'code':'코드·개발','image':'이미지 수집','research':'리서치','design':'디자인','general':'기획·문서·일반'}
DATA=[
('leader','총괄 리더','기획·운영','팀장,리더,매니저,pm,관리,총괄,업무배정','요청을 구체화하고 하위 담당자에게 지시한 뒤 결과를 재검토합니다.',['요청·완료 기준 정리','업무에 맞는 담당자 배정과 일정 조율','담당 범위가 비어 있으면 적합한 담당자를 지정하고 업무를 위임','진행 확인과 최종 결과 취합'],['불필요한 부서 인계와 고정 순서 강제 금지','실행·검증 근거 없이 완료 보고 금지'],list(KINDS),['planner','reporter']),
('product','서비스 기획자','기획·운영','기획,서비스,제품,pm,prd,요구사항,사업','서비스 요구사항과 사용 흐름을 설계합니다.',['사용자 요구사항·문제 정의','PRD·기능 명세·화면 흐름 작성','우선순위·완료 기준·일정 초안'],['직접 코드 수정은 개발 담당자에게 요청하고 요구사항 충족 여부를 대조','시장 수치·사용자 반응은 실제 출처가 있을 때만 보고'],['research','image','general'],['planner','reporter']),
('operations','운영 매니저','기획·운영','운영,일정,관리,업무,프로세스,오퍼레이션','반복 업무와 운영 문서를 정리합니다.',['업무 목록·체크리스트·일정 정리','운영 절차·FAQ·매뉴얼 작성','처리 현황 취합과 누락 확인'],['계정 권한 변경·결제·대외 발송은 별도 승인 범위 확인','개발 코드와 디자인 원본은 해당 담당자에게 요청'],['research','general'],['planner','reporter']),
('research','리서처','조사·분석','조사,리서치,자료,검색,시장조사,비교,출처,이미지,사진,수집,research','웹과 문서를 조사하고 출처가 있는 결과를 만듭니다.',['공개 웹·문서 검색과 실제 본문 열람','제품·서비스·기술 비교 조사','이미지·참고 자료 수집과 이용 조건 정리','근거 URL·날짜·미확인 사항을 포함한 보고서 작성'],['설치가 필요 없는 검색·열람은 기본 도구로 바로 실행','코드 구현과 디자인 원본 제작은 담당자에게 요청','이미지 내용을 육안 판별했다고 주장하지 않기'],['research','image','general'],['planner','reporter']),
('data','데이터 분석가','조사·분석','데이터,분석,통계,엑셀,표,차트,analytics','데이터를 정리하고 수치와 근거를 분석합니다.',['CSV·표 데이터 정리와 품질 확인','기술 통계·비교표·차트 작성','분석 스크립트와 재현 가능한 결과 정리'],['데이터에 없는 인과관계·미래 값을 사실로 단정 금지','원본 데이터 수정·삭제 전에 보존과 범위 확인'],['code','research','general'],['developer','reviewer']),
('ux','UX 리서처','조사·분석','ux,사용자,인터뷰,설문,리서치,사용성','사용자 문제를 조사하고 개선 근거를 정리합니다.',['제공된 인터뷰·설문 자료 분석','사용자 여정·사용성 문제·개선안 작성','가설과 검증된 근거 구분'],['진행하지 않은 사용자 조사·인터뷰를 꾸며내지 않기','실제 UI 구현은 디자이너·개발자에게 요청'],['research','general'],['planner','reporter']),
('ui','UI·UX 디자이너','디자인','디자인,ui,ux,웹디자인,앱디자인,화면,와이어프레임','화면 구조와 인터페이스 디자인을 설계합니다.',['화면 레이아웃·사용 흐름·와이어프레임 설계','색상·타이포·간격·컴포넌트 규칙 정리','반응형 화면과 인터랙션 명세 작성'],['지원되는 도구가 있을 때만 실제 디자인 파일 제작','동작하는 코드 구현·테스트는 개발·검증 담당자에게 요청'],['design','image','research','general'],['planner','reporter']),
('graphic','그래픽 디자이너','디자인','그래픽,이미지,썸네일,배너,로고,브랜딩,포스터','브랜드와 콘텐츠용 시각물을 제작합니다.',['썸네일·배너·포스터·브랜드 시안 구성','이미지 편집·합성 및 출력 규격 정리','사용 가능한 이미지·폰트의 출처와 이용 조건 확인'],['지원 도구·원본·사용 허락을 확인한 뒤 제작','제작하지 않은 시안이나 픽셀 검증을 완료했다고 보고 금지'],['design','image','research'],['developer','reviewer']),
('illustration','일러스트레이터','디자인','일러스트,캐릭터,2d,삽화,스프라이트,캐릭터디자인','2D 캐릭터와 일러스트 제작 명세를 만듭니다.',['캐릭터 외형·파츠·표정·포즈 기획','스프라이트·일러스트 제작과 규격 정리','실제 사용 가능한 도구로 에셋 편집'],['이미지 생성·편집 도구가 연결되지 않으면 제작 완료로 보고하지 않기','기존 캐릭터의 권리와 출처 확인'],['design','image','research'],['developer','reviewer']),
('frontend','프론트엔드 개발자','개발','프론트,프론트엔드,웹,react,vue,html,css,javascript','웹 화면과 사용자 인터랙션을 구현합니다.',['HTML·CSS·JavaScript와 화면 컴포넌트 구현','API 연동·접근성·반응형 동작 개선','브라우저 오류 재현과 회귀 검증'],['서버·DB 스키마 변경은 담당 범위를 확인하고 협업','실행하지 않은 브라우저 검증을 통과로 보고 금지'],['code','general'],['developer']),
('backend','백엔드 개발자','개발','백엔드,서버,api,db,데이터베이스,python,node,java','서버 API와 데이터 처리 로직을 구현합니다.',['API·서버 로직·DB 연동 구현','입력 검증·권한·오류 처리 개선','단위·통합 테스트와 실행 근거 정리'],['운영 데이터 삭제·배포·권한 확대는 별도 범위 확인','화면·시각 에셋 제작은 해당 담당자에게 요청'],['code','general'],['developer']),
('fullstack','풀스택 개발자','개발','풀스택,개발,코딩,프로그래밍,웹개발,구현,fullstack','클라이언트와 서버 기능을 함께 구현합니다.',['기능 요구사항에 맞는 화면·서버 코드 구현','오류 수정·테스트·실행 환경 연결','변경 파일과 실행·검증 근거 정리'],['단순 자료·사진 수집을 개발 작업으로 강제 분류하지 않기','배포·결제·외부 발송은 승인된 범위에서만 수행'],['code','general'],['developer']),
('automation','자동화 개발자','개발','자동화,스크립트,반복,배치,파이썬,툴,업무자동화','반복 작업을 재현 가능한 자동화로 만듭니다.',['파일·데이터 처리 스크립트 구현','반복 업무와 도구 연결 자동화','실패 복구·중복 실행·로그 처리'],['실제 누락된 프로그램이 있을 때만 설치 요청','파괴적인 파일 작업과 외부 전송의 범위 확인'],['code','general'],['developer']),
('unity','Unity 게임 개발자','게임 제작','게임,유니티,unity,c#,2d,3d,게임개발','Unity 프로젝트의 게임 로직을 담당합니다.',['게임플레이·UI·씬 로직 구현','Unity 프로젝트 코드·에셋 연결','연결된 에디터에서 실행·빌드·문제 재현'],['Unity 실행 환경과 라이선스 조건을 먼저 확인','에디터가 연결되지 않은 상태에서 실행·빌드 통과로 보고 금지','유료 에셋 구매·상용 플랜 전환 금지'],['code','design','general'],['developer']),
('unreal','Unreal 게임 개발자','게임 제작','게임,언리얼,unreal,블루프린트,blueprint,c++,3d','Unreal 프로젝트의 게임 로직을 담당합니다.',['Blueprint·C++ 실행 흐름과 게임플레이 구현','프로젝트 설정·에셋 연결·문제 재현','연결된 에디터에서 실행·빌드 결과 확인'],['Unreal 실행 환경과 사용 조건 확인','에디터 실행·실행 흐름 검증 없이 동작을 단정하지 않기','유료 에셋·플러그인 구매 금지'],['code','design','general'],['developer']),
('game-design','게임 기획자','게임 제작','게임,게임기획,레벨,밸런스,시스템,시나리오','게임의 규칙·콘텐츠·진행 구조를 설계합니다.',['게임 규칙·시스템·밸런스 명세 작성','레벨·보상·진행 구조 기획','개발·아트 담당자에게 필요한 제작 명세 전달'],['엔진 코드·완성 에셋 제작은 각 담당자에게 요청','플레이 테스트하지 않은 재미·밸런스를 검증 완료로 보고 금지'],['research','design','general'],['planner','reporter']),
('qa','QA 검증 담당','검증','qa,테스트,검증,버그,품질,리뷰,재현','실제 결과를 실행하고 요구사항과 대조합니다.',['재현 절차·테스트 케이스·회귀 점검','실제 명령·실행 결과·출처·산출물 대조','실패 원인과 미검증 범위를 보고'],['근거 없는 통과 판정·지원되지 않는 육안 판별 금지','구현 변경은 원칙적으로 담당자에게 수정 요청'],['code','general'],['reviewer','reporter']),
('security','보안 리뷰어','검증','보안,시큐리티,권한,취약점,security,감사','승인된 코드와 설정의 보안 문제를 검토합니다.',['입력·권한·비밀정보 처리 검토','승인된 범위의 보안 테스트와 재현','위험과 수정안을 근거와 함께 정리'],['외부 시스템 침투·계정 권한 확대를 임의 수행하지 않기','운영 비밀정보를 보고서·저장소에 노출하지 않기'],['code','research','general'],['reviewer','reporter']),
('reporter','문서·보고 담당','문서·콘텐츠','문서,보고,기록,리포트,요약,매뉴얼,작성','결과와 근거를 이해하기 쉬운 문서로 정리합니다.',['검증된 결과·출처·변경 사항 취합','보고서·가이드·매뉴얼·요약문 작성','누락된 근거와 사용자 확인 사항 표시'],['실행 담당자의 주장만으로 완료·검증 통과를 선언하지 않기','코드 구현·시각 에셋 제작은 해당 담당자에게 요청'],['research','image','general'],['reporter']),
('content','콘텐츠 에디터','문서·콘텐츠','콘텐츠,카피,글쓰기,에디터,편집,마케팅,홍보','목적에 맞는 글과 콘텐츠 구성을 만듭니다.',['기사·소개문·카피·콘텐츠 초안 작성','사실 확인·교정·용어·톤 정리','이미지 자료와 출처·사용 조건 정리'],['광고 집행·게시·메일 발송은 승인된 경우에만 수행','확인되지 않은 제품 기능·수치·인용문 작성 금지'],['research','image','general'],['planner','reporter']),
]
CATALOG=[{'id':r[0],'name':r[1],'group':r[2],'aliases':r[3].split(','),'summary':r[4],'responsibilities':'\n'.join(r[5]),'boundaries':'\n'.join(r[6]),'work_kinds':r[7],'capabilities':r[8],'job_title':r[1],'specialty':r[1]} for r in DATA]
def search(query=''):
 q=re.sub(r'\s+','',query.lower())[:100];parts=[p for p in re.split(r'[\s,/]+',query.lower()) if p]
 def score(p):
  head=' '.join([p['name'],p['group'],*p['aliases']]).lower();flat=re.sub(r'\s+','',head)
  return (10 if q and q in flat else 0)+sum(2 for term in parts if term in head)
 rows=sorted(CATALOG,key=lambda p:-score(p)) if q else CATALOG
 return copy.deepcopy([p for p in rows if not q or score(p)>0])
def default_id(agent):
 roles=agent.get('capabilities',[]);text=(agent.get('job_title','')+' '+agent.get('specialty','')).lower()
 if any(w in text for w in ('총괄','팀장','리더')):return 'leader'
 for words,preset in [(('프론트','frontend'),'frontend'),(('백엔드','backend'),'backend'),(('유니티','unity'),'unity'),(('언리얼','unreal'),'unreal'),(('게임 기획','게임기획'),'game-design'),(('그래픽','썸네일'),'graphic'),(('일러스트','캐릭터'),'illustration'),(('데이터','통계'),'data'),(('보안','security'),'security'),(('서비스 기획','제품 기획','기획'),'product')]:
  if any(w in text for w in words):return preset
 if 'planner' in roles and ('developer' in roles or 'reviewer' not in roles):return 'leader'
 if '리서' in text or '조사' in text:return 'research'
 if '디자인' in text:return 'ui'
 if 'developer' in roles:return 'fullstack'
 if 'reviewer' in roles:return 'qa'
 return 'reporter'
def enrich(agent):
 a=copy.deepcopy(agent);p=next((p for p in CATALOG if p['id']==a.get('role_preset')),None) or next(p for p in CATALOG if p['id']==default_id(a))
 if not a.get('role_preset'):
  a['role_preset']=p['id']
  for k in ('job_title','specialty'):
   if not a.get(k):a[k]=p[k]
 for k in ('responsibilities','boundaries','work_kinds'):a.setdefault(k,copy.deepcopy(p[k]))
 return a
