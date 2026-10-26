"""Publish actual Slack channel receipts, owner DMs and VM artifact evidence."""
import base64
from datetime import datetime,timezone,timedelta
import html
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'reports/slack-vm-implementation'


def main():
    live=json.loads((OUT/'slack-team-live-status.json').read_text(encoding='utf-8'))
    direct=json.loads((OUT/'real-user-dm-receipts.json').read_text(encoding='utf-8'))
    if live.get('status')!='passed':raise ValueError('Actual Slack verification has not passed')
    receipts=live['receipts'];paths=[p for r in receipts for p in json.loads(r['received_images'])]
    if not paths or len(direct)!=2:raise ValueError('Actual peer images or individual DM evidence missing')
    now=datetime.now(timezone(timedelta(hours=9)));elapsed=int((now-datetime(2026,10,24,19,46,40,tzinfo=now.tzinfo)).total_seconds())
    def image(path,label):
        raw=path.read_bytes();mime='image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg'
        return '<figure><img style="max-width:100%;height:auto" src="data:'+mime+';base64,'+base64.b64encode(raw).decode()+'" alt="'+html.escape(label)+'"><figcaption>'+html.escape(label)+'</figcaption></figure>'
    section=f'''<section id="actual-slack-complete" class="card"><span class="badge">실제 Slack·VM 검증 통과</span><h2>지우·서연 연결이 완료됐습니다.</h2><p>계정 인증과 팀원 봇 연결을 따로 표시하도록 수정했습니다. Slack의 실제 <code>cannot_dm_bot</code> 응답을 확인하고 사용자 승인을 받아 <b>팀원 간 업무 채널 대화·이미지 공유 + 사용자와 개별 DM</b>으로 전환했습니다.</p><table><tr><th>실제 검증</th><th>결과</th></tr><tr><td>팀원 봇 인증·채널·Socket Mode</td><td>지우·서연 모두 연결</td></tr><tr><td>팀원 메시지 수신</td><td>{len(receipts)}건 · 서연의 실제 Slack 이벤트 ID 확인</td></tr><tr><td>상대 팀원의 이미지 수신</td><td>{len(paths)}개 · 서연의 인증으로 다운로드</td></tr><tr><td>사용자 개별 DM</td><td>지우·서연이 각각 텍스트와 실제 VM 화면을 전송, Slack 서버 확인 ID 저장</td></tr><tr><td>실제 모델·VM·결과 ZIP</td><td>{live['elapsed_seconds']}초 · 파일 내용 대조 통과</td></tr></table><p>검증 작업: <code>{live['task_id']}</code> · <a href="real-slack-channel-vm.log">실제 실행 로그</a> · <a href="slack-team-live-status.json">채널 수신·VM 근거</a> · <a href="real-user-dm-receipts.json">개별 DM 전송 근거</a></p>{image(OUT/'real-slack-connected.jpg','실제 앱 화면 · 팀원 봇 연결됨')}{image(ROOT/'data/tasks'/live['task_id']/paths[0],'서연이 실제 Slack에서 다운로드한 VM 화면')}<p class="meta">제안 시간: 3~5분, 채널 전환·실제 검증 추가 5~10분, 진행 중 추가 예상 3~5분.<br>실제 완료 기록: {elapsed//60}분 {elapsed%60}초 · {now:%Y-%m-%d %H:%M:%S} KST.<br>XERJ: 미사용 — 제공 도구 없음, rg와 현재 소스·실행 근거로 확인.</p></section>'''
    for file in ['index.html','dm-update.html']:
        path=OUT/file;text=path.read_text(encoding='utf-8')
        if 'id="actual-slack-complete"' in text:text=re.sub(r'<section id="actual-slack-complete".*?</section>',lambda _:section,text,count=1,flags=re.S)
        else:text=text.replace('<main>','<main>'+section,1)
        path.write_text(text,encoding='utf-8')
    print('Actual Slack evidence report updated:',live['task_id'],len(receipts),'peer events,',len(paths),'received images,',len(direct),'owner DMs')


if __name__=='__main__':main()
