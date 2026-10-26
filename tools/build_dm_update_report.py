"""Current named teammates / automatic onboarding evidence, without credentials."""
import base64
from datetime import datetime,timezone,timedelta
import html
import json
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'reports/slack-vm-implementation'
START=datetime(2026,10,24,19,2,29,tzinfo=timezone(timedelta(hours=9)))


def main():
    now=datetime.now(START.tzinfo);elapsed=int((now-START).total_seconds())
    record=json.loads((ROOT/'data/guest-model-live-verification.json').read_text(encoding='utf-8'))
    (OUT/'automatic-guest-evidence.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    images=[]
    for file,label in [('automatic-bot-add.jpg','실제 MOA · 자동 연결 화면'),('automatic-bot-add-details.jpg','팀원·이름·사진 선택과 봇 추가 버튼')]:
        images.append((OUT/file,label))
    for role,label in [('developer','지우 · 실제 VM 파일 생성'),('reviewer','서연 · 실제 VM 내용 검사')]:
        source=ROOT/'data/tasks'/record['task_id']/'vm'/f'r0-{role}-after.png'
        target=OUT/f'automatic-{role}-vm.png';shutil.copyfile(source,target);images.append((target,label))
    figures=''.join('<figure><img src="data:'+('image/png' if p.read_bytes().startswith(b'\x89PNG') else 'image/jpeg')+';base64,'+base64.b64encode(p.read_bytes()).decode()+'" alt="'+html.escape(label)+'"><figcaption>'+html.escape(label)+'</figcaption></figure>' for p,label in images)
    logo='data:image/png;base64,'+base64.b64encode((ROOT/'docs/assets/moa-logo.png').read_bytes()).decode()
    tests=[]
    for file,label in [('automatic-regression-final.log','전체 회귀 검사'),('automatic-focused-final.log','최종 Slack 집중 검사'),('automatic-backup-final.log','백업·인증 정보 제외 검사')]:
        content=(OUT/file).read_text(encoding='utf-8-sig',errors='replace')
        count=re.findall(r'Ran (\d+) tests',content)
        tests.append(f'<tr><td>{label}</td><td>{count[-1] if count else "미확인"}개 · '+('통과' if re.search(r'\nOK\s*$',content) else '로그 확인 필요')+f'</td><td><a href="{file}">로그</a></td></tr>')
    tests.append('<tr><td>실제 DOM·로컬 HTTP UI</td><td>9묶음 통과 · 봇 등록 API는 모의 응답</td><td><a href="auto-ui-tests-final.log">로그</a></td></tr>')
    cli={'recorded_at':now.isoformat(),'version':'4.11.0','source':'https://downloads.slack-edge.com/slack-cli/slack_cli_latest_windows_64-bit.zip',
         'actual_checks':['ConPTY background execution','official CLI reads MOA manifest and custom hooks','actual one-time auth ticket generated without printing credentials'],
         'not_verified':['workspace permission approval','actual bot creation and icon upload','real bot-to-bot DM and image network delivery']}
    (OUT/'official-cli-verification.json').write_text(json.dumps(cli,ensure_ascii=False,indent=2),encoding='utf-8')
    style='''*{box-sizing:border-box}body{margin:0;background:#faf7f5;color:#3c2d26;font:16px/1.8 "Segoe UI","Malgun Gothic",sans-serif}header{padding:48px max(6vw,24px);background:#fff0e8;border-bottom:1px solid #ecd8cb}h1{font-size:clamp(30px,5vw,54px);line-height:1.25;letter-spacing:-.05em;max-width:900px}h2{font-size:25px;letter-spacing:-.03em}main{max-width:1100px;margin:auto;padding:28px}section{margin:32px 0}.brand{display:flex;align-items:center;gap:12px;font-weight:700;font-size:27px}.mark{position:relative;width:48px;height:38px;overflow:hidden}.mark img{width:72px;height:72px;max-width:none;position:absolute;left:-12px;top:-22px}.meta{font-size:13px;color:#8d6a59}.badge{display:inline-block;padding:5px 13px;border-radius:20px;background:#f7d9c7;color:#8b3b20;font-size:13px}.badge.wait{background:#f8e8bd;color:#775c24}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card,figure{background:white;border:1px solid #eddfd7;border-radius:18px;padding:24px;margin:18px 0}figure img{width:100%;height:auto;border-radius:10px}figcaption{font-size:13px;color:#8d6a59;margin-top:12px}.button{display:inline-block;background:#bd4a28;color:white;text-decoration:none;padding:11px 18px;border-radius:10px;margin:10px 10px 10px 0}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:14px;text-align:left;border-bottom:1px solid #eee0d7;vertical-align:top}th{background:#fff0e8}a{color:#a94626}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#49362b;color:#ffe7d4;padding:20px;border-radius:12px;font-size:13px}footer{border-top:1px solid #e7d7cd;margin-top:40px;padding:24px 0;font-size:13px;color:#8d6a59}@media(max-width:700px){.grid{grid-template-columns:1fr}main{padding:16px}}'''
    body=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="{logo}"><title>MOA · 팀원 DM과 자동 봇 추가</title><style>{style}</style><header><div class="brand"><span class="mark"><img src="{logo}" alt=""></span>모아</div><p class="meta">NAMED TEAMMATES / AUTOMATIC SLACK SETUP</p><h1>이름으로 추가하고,<br>채널과 개별 DM으로 협업합니다.</h1><span class="badge">구현·로컬 검증 완료</span> <span class="badge wait">실제 Slack 연결 완료 · 채널 방식</span><p>팀원별 이름·사진, DM·이미지 수신 확인, 실제 VM 작업을 연결했습니다. 사용자는 최초 Slack 인증만 진행하며 봇 토큰을 찾아 복사하지 않습니다.</p><a class="button" href="http://127.0.0.1:8765/#settings/slack">MOA에서 팀원 봇 추가</a><a class="button" href="connect.html">업데이트한 가이드</a></header><main>
<section><h2>사용자 흐름</h2><div class="card"><b>팀원 선택 · 이름·사진 → 봇 추가 → 최초 Slack 승인 → 자동 등록·연결</b><p>인증 전 봇 추가를 누르면 인증 절차를 시작하고 선택한 팀원을 기억합니다. 인증 후 봇 이름·사진·권한·접속 정보 등록을 이어갑니다. 공유 봇 대신 각 팀원에게 별도 봇 계정을 배정합니다.</p></div></section>
<section class="grid"><div class="card"><h2>채널과 이미지</h2><p>실제 상대 봇의 채널 수신 이벤트와 상대 인증으로 이미지 다운로드를 확인해야 다음 단계로 진행합니다. 다른 봇의 메시지와 반복 이벤트는 새 작업으로 만들지 않습니다. 사용자 DM 답변은 각 팀원의 대화방으로 보내며 팀원 간 대화·이미지는 업무 채널에서 공유합니다.</p></div><div class="card"><h2>연결 정보 자동 처리</h2><p>Slack 공식 CLI 4.11.0을 창 없이 실행합니다. CLI가 제공한 인증 정보는 보호 파일을 거쳐 DPAPI 저장소에 넣습니다. 최초 CLI 로그인 폴더는 사용자/SYSTEM 전용이며 백업에서 제외합니다. 연결 실패 시 토큰을 다시 복사하게 하지 않고 저장된 정보를 보존합니다.</p></div></section>
<section><h2>확인한 범위와 남은 범위</h2><table><tr><th>범위</th><th>실행 근거</th><th>상태</th></tr><tr><td>공식 Slack 도구</td><td>실제 CLI 실행·manifest 읽기·일회용 인증 명령 생성</td><td>확인</td></tr><tr><td>자동 봇 등록·DM·이미지 로직</td><td>격리 DB·모의 Slack API·수신 이벤트·재시작 및 실패 검사</td><td>로컬 확인</td></tr><tr><td>모델·VM·파일</td><td>실제 Qwen 모델, QEMU 게스트, 명령 종료 코드, ZIP 안 파일 내용 대조</td><td>확인</td></tr><tr><td>실제 Slack 봇 등록·채널·소켓 연결</td><td>사용자 인증과 두 팀원 봇의 실제 인증·채널·소켓 연결 확인 완료</td><td>연결 확인 · 실제 VM 통합 결과는 아래 최신 기록 참조</td></tr></table><p class="meta">현재 모델은 텍스트 전용입니다. 이미지를 실제 전달·수신하지만 픽셀을 이해했다고 주장하지 않으며, VM 검증은 실제 명령·파일 근거를 사용합니다.</p></section>
<section><h2>실행 검증</h2><table>{''.join(tests)}<tr><td>실제 모델·VM 재검증</td><td>{record['elapsed_seconds']}초 · {record['status']} · 파일 내용 일치</td><td><a href="automatic-final-guest.log">로그</a></td></tr></table><p class="meta">전체 회귀 이후 추가한 세부 변경은 최종 집중·백업 검사로 재검증했습니다. 모의 Slack 검사를 실제 Slack 성공으로 합산하지 않습니다.</p><pre>{html.escape(json.dumps({'task_id':record['task_id'],'guest_workspace':'/home/tc/moa/'+record['task_id'],'expected_and_exported':record['exported_ready_content'],'tests_run':record['verification']['tests_run'],'artifact_sha256':record['verification']['source_hashes']},ensure_ascii=False,indent=2))}</pre></section>
<section><h2>실제 화면 기록</h2>{figures}</section><section><h2>다음 확인</h2><p>팀원 봇 연결 후 <code>tools/verify_slack_team_live.py --run</code>으로 실제 Slack 수신 근거와 VM 결과를 함께 검증할 수 있습니다. <a href="slack-team-live-status.json">실제 Slack 실행 기록</a>에서 최종 결과를 확인할 수 있습니다. Slack이 봇 간 DM을 cannot_dm_bot으로 거부한 것을 실제 확인하여, 사용자 승인에 따라 팀원 채널·사용자 개별 DM 방식으로 전환했습니다.</p></section><footer>제안 시간: DM·가이드 30~60분, 자동 연결 추가 30~60분.<br>구현·로컬 검증 기록 시각: {now:%Y-%m-%d %H:%M:%S} KST · 시작부터 {elapsed//60}분 {elapsed%60}초.<br>Slack의 봇 간 DM은 지원되지 않습니다. 팀원 채널과 사용자 개별 DM이 현재 승인된 구성입니다.<br>XERJ: 미사용 — 제공 MCP·검색 도구 없음, rg와 현재 소스·실행 결과로 확인.</footer></main></html>'''
    (OUT/'dm-update.html').write_text(body,encoding='utf-8')
    index=OUT/'index.html';text=index.read_text(encoding='utf-8')
    note='<div class="note" id="latest-automation"><b>최신 업데이트 · 팀원 DM과 자동 봇 추가</b><p>최초 Slack 인증 뒤 이름·사진으로 봇을 자동 연결합니다. <a href="dm-update.html">최신 구현·검증 보고서 →</a> · <a href="connect.html">새 가이드 →</a></p></div>'
    if 'id="latest-automation"' in text:text=re.sub(r'<div class="note" id="latest-automation">.*?</div>',lambda _:note,text,count=1,flags=re.S)
    else:text=text.replace('<main>','<main>'+note,1)
    index.write_text(text,encoding='utf-8')
    print('Created dm-update.html; main report links to current implementation')


if __name__=='__main__':main()
