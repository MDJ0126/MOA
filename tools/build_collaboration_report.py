"""Portable implementation report and pre-filled Slack app creation links."""
import base64
from datetime import datetime,timezone,timedelta
import html
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import quote
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'src'))
from setup_config import manifest
OUT=ROOT/'reports/slack-vm-implementation';OUT.mkdir(exist_ok=True)
esc=html.escape
now=datetime.now(timezone(timedelta(hours=9)))
start=datetime(2026,10,24,16,44,0,tzinfo=now.tzinfo)

style='''*{box-sizing:border-box}body{margin:0;background:#f5f6f0;color:#233c37;font:16px/1.8 "Segoe UI","Malgun Gothic",sans-serif}header{background:#213f38;color:white;padding:58px 7vw}header p{color:#d3e4d9;max-width:900px}h1{font-size:clamp(32px,5vw,56px);line-height:1.25;letter-spacing:-.05em}main{max-width:1100px;margin:auto;padding:26px}section{margin:40px 0}h2{letter-spacing:-.04em;font-size:27px}h3{margin-top:0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card,figure{padding:24px;border:1px solid #d9e2d8;background:white;border-radius:18px;margin:18px 0}.badge{display:inline-block;padding:5px 12px;border-radius:20px;font-size:13px;font-weight:bold;background:#e4efe6;color:#2b6549}.pending{background:#fff0dd;color:#975517}.button{display:inline-block;background:#285d49;color:white;border-radius:10px;padding:11px 18px;text-decoration:none;margin:7px 7px 7px 0}.soft{background:#e6eee4;color:#23483c}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;vertical-align:top;padding:14px;border-bottom:1px solid #d9e2d8}.table{overflow:auto;background:white;border:1px solid #d9e2d8;border-radius:14px}th{background:#eaf0e6}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 Consolas,monospace;background:#203a32;color:#d6e8dc;padding:20px;border-radius:12px}code{overflow-wrap:anywhere}img{max-width:100%;display:block;border-radius:12px;max-height:720px;margin:auto;object-fit:contain}.note{border-left:4px solid #e28a68;background:#fff2e7;padding:17px 22px;border-radius:0 12px 12px 0}a{color:inherit}small,.meta{color:#667b6e;font-size:13px}details{padding:16px;border:1px solid #d9e2d8;border-radius:12px;background:white;margin:12px 0}summary{cursor:pointer;font-weight:bold}footer{padding:25px 0 45px;color:#667b6e;font-size:13px;border-top:1px solid #d9e2d8}@media(max-width:700px){.grid{grid-template-columns:1fr}main{padding:16px}}'''

style += '.report-brand{display:flex;align-items:center;gap:12px;text-decoration:none;margin-bottom:26px;color:#593426}.report-brand b{font-size:28px}.report-brand>span:last-child{font-size:10px;letter-spacing:.14em}.report-logo{position:relative;width:48px;height:38px;overflow:hidden}.report-logo img{position:absolute;width:72px;height:72px;max-width:none;max-height:none;left:-12px;top:-22px;border-radius:0}body{background:#faf7f5;color:#392b25}header{background:#fff0e8;color:#593426;border-bottom:1px solid #eed7c9}header p{color:#88624f}.button{background:#c44d2b;color:white}.soft{background:#f9dccb;color:#763c25}.card,figure,.table,details{border-color:#eddfd7}th{background:#fff0e8}.badge{background:#fbe0ce;color:#954622}.note{background:#fff1e7;border-color:#dc7149}small,.meta,footer{color:#967460}pre{background:#49352b;color:#ffe6d4}'
logo_data='data:image/png;base64,'+base64.b64encode((ROOT/'docs/assets/moa-logo.png').read_bytes()).decode()
report_brand = '<a class="report-brand" href="http://127.0.0.1:8765/"><span class="report-logo"><img src="'+logo_data+'" alt=""></span><b>모아</b><span>LOCAL AGENT TEAM</span></a>'

favicon = '<link rel="icon" type="image/png" href="'+logo_data+'">'

from build_slack_guide import onboarding as build_onboarding
onboarding=build_onboarding()
(OUT/'connect.html').write_text(f'<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{favicon}<title>모아 · Slack 가입과 봇 연결</title><style>{style}</style><header>{report_brand}<small>MOA / CONNECT YOUR TEAM</small><h1>내 팀원을 Slack에 연결하세요.</h1><p>MOA에서 이름·사진을 정하고 봇 추가를 누르세요. 최초 Slack 승인 뒤 설정과 연결을 자동으로 이어갑니다.</p></header><main>{onboarding}</main></html>',encoding='utf-8')

def read_record(name):
    path=ROOT/'data'/name
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
guest=read_record('guest-live-verification.json')
model=read_record('guest-model-live-verification.json')
if guest:(OUT/'guest-evidence.json').write_text(json.dumps(guest,ensure_ascii=False,indent=2),encoding='utf-8')
if model:(OUT/'guest-model-evidence.json').write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding='utf-8')
shots=[]
if (OUT/'slack-settings.jpg').exists():shots.append(('slack-settings.jpg','실행 중인 앱의 Slack 협업 설정'))
if model:
    folder=ROOT/'data/tasks'/model['task_id']/'vm'
    for role,label in [('developer','개발 담당의 실제 VM 작업 후'),('reviewer','검증 담당의 실제 VM 작업 후')]:
        candidates=sorted(folder.glob('r*-'+role+'-after.png'),key=lambda p:p.stat().st_mtime)
        if candidates:
            name='actual-'+role+'.png';shutil.copyfile(candidates[-1],OUT/name);shots.append((name,label))
elif guest:
    source=ROOT/'data/tasks'/guest['task_id']/'vm/r0-reviewer-after.png'
    if source.exists():shutil.copyfile(source,OUT/'actual-reviewer.png');shots.append(('actual-reviewer.png','실제 VM 명령·대조 시험 후 (모델 추론 제외)'))
gallery=''
for name,label in shots:
    mime='image/jpeg' if name.endswith('.jpg') else 'image/png'
    src='data:'+mime+';base64,'+base64.b64encode((OUT/name).read_bytes()).decode()
    gallery+=f'<figure><figcaption><h3>{label}</h3></figcaption><img src="{src}" alt="{label}"></figure>'
logs=''
for file,title in [('unit-tests-final.log','전체 회귀 테스트'),('new-feature-tests.log','새 기능 집중 검증'),('ui-tests-final.log','최종 UI 검증'),('http-tests.log','HTTP 통합'),('guest-live.log','실제 게스트 명령'),('guest-model-live-final.log','실제 모델·VM 전체 흐름')]:
    if (OUT/file).exists():logs+=f'<details><summary>{title}</summary><pre>{esc((OUT/file).read_text(encoding="utf-8-sig",errors="replace")[-14000:])}</pre></details>'
elapsed=int((now-start).total_seconds())
model_status=(f'실제 로컬 모델이 VM에서 구현·검증한 작업 {model["task_id"]} 완료 · {model["elapsed_seconds"]}초. HTTP ZIP의 게스트 아카이브에서 파일을 다시 읽어 대조했습니다.' if model else '실제 모델·VM 전체 흐름 검증 중 또는 미완료입니다. 아래 실행 로그를 확인하세요.')
document=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{favicon}<title>모아 · Slack 협업과 실제 VM 구현 보고서</title><style>{style}</style></head><body><header>{report_brand}<small>MOA / SLACK COLLABORATION + REAL VM</small><h1>Slack에서 이어받고,<br>실제 VM에서 작업합니다.</h1><p>대화 전송 확인을 기다리는 단계 인계, 이미지 공유, 실제 게스트 명령 실행과 결과 보존을 구현했습니다.</p><span class="badge">구현 반영</span> <span class="badge pending">실제 Slack 송수신: 가입·인증 대기</span></header><main><section><h2>구현한 동작</h2><div class="table"><table><tr><th>기능</th><th>동작</th></tr><tr><td>Slack 중심 팀원 대화</td><td>공개 계획·명령 실행 결과·검토 결론을 작업 스레드에 전송합니다. 다음 담당자는 Slack 전달·이미지 업로드 확인 후 진행하고, 확인된 대화 기록을 맥락으로 읽습니다.</td></tr><tr><td>이미지 교환</td><td>VM 명령 후 및 단계 전후 PNG를 스레드에 업로드합니다. 허용 사용자의 PNG·JPEG 첨부는 인증 다운로드로 작업에 저장합니다. 8MB·메시지당 4개 제한, 외부 임의 URL 차단.</td></tr><tr><td>실제 VM 업무</td><td>모델이 선택한 명령을 QEMU 게스트에 입력하고, 게스트 직렬 출력과 종료 코드를 읽습니다. 작업 파일은 <code>/home/tc/moa/작업ID</code>에 생성하며 호스트에서 작업 명령을 실행하지 않습니다.</td></tr><tr><td>결과 보존</td><td>게스트 작업 폴더를 검증된 <code>guest-workspace.tar.gz</code>로 가져와 보고서 ZIP에 넣습니다. VM 종료 전 결과를 보존합니다.</td></tr><tr><td>중복·루프 방지</td><td>봇 메시지 에코로 새 작업을 만들지 않습니다. 전송 확인을 DB에 저장하고, 불확실한 이미지 업로드는 고유 이름으로 확인해 중복 업로드를 막습니다.</td></tr><tr><td>중단과 역할 경계</td><td>계획·보고 담당자는 실행 도구를 쓰지 않습니다. 개발·검증 담당자는 실제 명령 실행 없이 완료할 수 없습니다. 취소·일시 중지 중에는 새 입력을 막고, 불확실한 VM 입력을 자동 재실행하지 않습니다.</td></tr></table></div></section><section><h2>실행 검증</h2><p>{esc(model_status)}</p><p class="note"><b>실제 Slack은 아직 연결하지 않았습니다.</b> Slack 전송·수신·이미지 교환 경로는 가짜 API 응답을 쓰는 통합 테스트로 검증했으며 실제 워크스페이스 송수신 성공으로 보고하지 않습니다. 가입·봇 인증 완료 후 실연결 시험이 남습니다.</p><p>기본 Qwen Coder 모델은 이미지의 시각적 내용을 이해하지 않습니다. 이미지 자체는 팀에 공유하되 판단은 실제 터미널 출력·파일·종료 코드에 근거합니다. 번들 Tiny Core Linux 게스트 명령을 지원하며 Windows·Mac 게스트 실행을 이 검증으로 보장하지 않습니다.</p><h3>실제 실행 중 수정한 문제</h3><ul><li>기존 게스트 키 입력의 &lt; / &gt; 매핑이 반대여서 출력 전달이 실패한 문제를 수정하고 실제 게스트 명령을 재검증했습니다.</li><li>모델이 명령 없이 완료를 선언하거나 계획 담당이 구현 명령을 반복하는 것을 막기 위해 역할별 응답 스키마를 강제했습니다.</li><li>UI 요청 ID가 있는 작업도 Slack 스레드에 연결되도록 수정했습니다.</li></ul></section>{onboarding}<section><h2>실제 화면</h2>{gallery}</section><section><h2>검증 로그</h2>{logs}</section><section><h2>검증 도구와 구현 파일</h2><p><code>src/slack_workflow.py</code> · <code>slack_media.py</code> · <code>guest_agent.py</code> · <code>vm_runtime.py</code> · <code>slack_service.py</code> · <code>app.py</code></p><p><code>tools/verify_guest_live.py</code>는 실제 게스트 명령을, <code>tools/verify_guest_model_live.py</code>는 실제 모델·VM·산출물 다운로드를 검증합니다. 계정 연결 전에 실제 Slack을 흉내 내서 성공 처리하지 않습니다.</p></section><footer>제안 시간: 30~60분 · 시작 {start:%Y-%m-%d %H:%M:%S} KST<br>보고서 생성: {now:%Y-%m-%d %H:%M:%S} KST · {elapsed//60}분 {elapsed%60}초 경과<br>가입·인증 전 실제 Slack 실연결 검증은 미완료입니다.<br>XERJ: 미사용 — 도구·검색 스크립트 미제공, rg로 소스 확인<br><a href="https://docs.slack.dev/tools/python-slack-sdk/web/">Slack SDK 공식 문서</a> · <a href="https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/">Slack manifest 공식 문서</a></footer></main></body></html>'''
(OUT/'index.html').write_text(document,encoding='utf-8')
print(OUT/'index.html')
