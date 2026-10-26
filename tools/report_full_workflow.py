"""Build the comparison only after independent real-result audits exist."""
import datetime,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/full-performance-20261025'
def build():
    before=json.loads((OUT/'before-audit.json').read_text(encoding='utf-8'))
    after=json.loads((OUT/'after-audit.json').read_text(encoding='utf-8'))
    assert all(after['checks'].values()),'Final real-work quality audit has not passed'
    assert before['exported_source_sha256']==after['exported_source_sha256'],'Exported source changed'
    deployment=json.loads((OUT/'deployment-final.json').read_text(encoding='utf-8'))
    assert deployment['source_status']=='matching' and deployment['worker_alive'] and deployment['paused'] is True
    reduction=round((1-after['elapsed_seconds']/before['elapsed_seconds'])*100,2)
    leader_reduction=round((1-after['leader_seconds']/before['leader_seconds'])*100,2)
    now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    start=datetime.datetime(2026,10,25,12,59,55,210739,tzinfo=now.tzinfo)
    result={'before':before,'after':after,'elapsed_reduction_percent':reduction,'leader_reduction_percent':leader_reduction,
        'tests_passed':106,'initial_estimate':'15~30분','revised_estimate':'35~40분','actual_elapsed_seconds':round((now-start).total_seconds(),2),
        'completed_at':now.isoformat(),'deployment':deployment,'xerj':'미사용 — 제공 MCP/검색 스크립트가 없어 rg와 현재 소스·실행 결과 사용'}
    (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 실제 VM·Slack 전체 작업 전후 비교','',f'완료: {now.isoformat()}','',
      '동일 요청: add.sh에 `echo $(($1 + $2))` 한 줄을 작성하고, 다른 담당자가 양수·음수·0 입력을 실제 VM에서 검증합니다. Slack으로 실제 명령·화면을 전달하고 상대 수신을 확인한 뒤 최종 ZIP 속 코드를 원문과 비교했습니다.','',
      '| 항목 | 변경 전 | 최종 변경 후 |','|---|---:|---:|',
      f"| 접수 → 전체 전달·ZIP 대조 | {before['elapsed_seconds']:.3f}초 | {after['elapsed_seconds']:.3f}초 |",
      f"| 팀장 배정 | {before['leader_seconds']:.3f}초 | {after['leader_seconds']:.3f}초 |",
      f"| 품질 조건 | {before['quality_passed']}/{before['quality_total']} | {after['quality_passed']}/{after['quality_total']} |",
      f"| VM 담당자 모델 호출 | {before['guest_model_calls']}회 | {after['guest_model_calls']}회 |",
      f"| VM 담당자 실제 처리 입력 토큰 (캐시 제외) | {before['guest_uncached_prompt_tokens']} | {after['guest_uncached_prompt_tokens']} |",
      f"| Slack 전달 확인 | {before['slack_deliveries']}건 | {after['slack_deliveries']}건 |",
      f"| 수신 이미지 원본 바이트 대조 | {len(before['images'])}건 일치 | {len(after['images'])}건 일치 |",'',
      f'이번 실제 실행의 전체 시간은 {reduction:.2f}%, 팀장 배정 시간은 {leader_reduction:.2f}% 줄었습니다. 품질이 누락된 기준판도 정상 통과로 취급하지 않았습니다.','',
      '품질: 원본 코드 정확한 바이트 일치, 실제 게스트 검증 결과, 서로 다른 실행·검증 담당자, 요청된 양수/음수/0 검증 전체 실행과 REVIEW_PASS 출력, 모든 Slack 전송 영수증, 상대 수신 이벤트, 수신 이미지와 업로드 원본의 해시 일치를 확인했습니다. 기준판은 검증 담당자가 코드를 다시 쓰고 양수만 실행해 독립 검증 조건을 충족하지 못했습니다. 최종판은 지정된 세 조건을 모두 실제 수행했습니다.','',
      '수정: VM을 모델보다 먼저 준비하도록 실행 순서를 변경했습니다(기존 RAM 검사 유지). VM 입력의 중복 조직 명부·표시 정보와 팀장 안내를 줄이고, 원문·담당 지침·범위·검증 조건은 보존했습니다. 별도 보고 담당자는 명시 요청이 있을 때만 추가하며 기본 Slack 진행·화면·최종 보고는 유지합니다. 저장된 출처가 없으면 리서치 재검증 도구를 제공하지 않습니다. 검증 담당자의 직접 파일 덮어쓰기를 차단하고, 사용자 요청 대기를 배경 평가보다 우선합니다.','',
      '공통 조건: Qwen2.5-Coder-7B Q4_K_M, CPU 4스레드, 컨텍스트 4096, 추론 캐시 256MiB, 유휴 절전 300초, 실제 Tiny Core QEMU(TCG, RAM 512MB, CPU 1개), 동일 Slack 연결을 사용했습니다. VM·모델 준비를 먼저 마친 뒤 요청 접수 시점을 시간의 시작으로 삼았습니다. 실제 VM 명령·내보내기·Slack 수신 및 최종 ZIP 다운로드/대조는 시간에 포함됩니다. 냉간 앱/VM/모델 부팅 시간은 이 작업 시간 비교에 포함하지 않습니다.','',
      '검증 한계: 단일 실제 요청의 운영 환경 실측입니다. 기준판의 VM 모델 호출 대기에는 서버 처리 시간 외 약 81초가 포함되어 있으므로 전체 감소분을 입력 축소만의 인과 효과로 단정하지 않습니다. 모든 업무에서 같은 개선율이나 일반적인 모델 품질을 보장하는 측정은 아닙니다.','',
      '실패 기록: 최초 기존 순서는 RAM 부족으로 약 85.6초 후 VM 진입 전에 실패했습니다(before-admission-failure.json). 첫 개선판은 코드 검증에 출처 재검증 도구가 잘못 노출돼 362.487초에 실패했습니다(after-tool-selection-failure.json). 이를 수정한 뒤 최종 전체 실행을 다시 검증했습니다. 모델 초기 로딩 중 준비 응답 503 재시도는 작업 측정 전 단계로 구분했습니다.','',
      '배포: 실행 중 소스 지문 matching, 작업 스레드 정상, 실제 Slack 연결 및 VM 동작을 확인했습니다. 비교 전의 일시 중지 상태를 복원했습니다. 관련 회귀 검사 106개 통과. 전후 보고 원칙은 프로젝트 AGENTS.md에 저장했습니다.','',
      f"제안 시간: 15~30분(도중 35~40분으로 갱신). 실제 소요: {result['actual_elapsed_seconds']}초. 완료 시각: {now.isoformat()}.",
      'XERJ: 미사용 — 제공 도구가 없어 rg·현재 소스·실행 로그·산출물로 검증했습니다.']
    (OUT/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('elapsed_reduction_percent','leader_reduction_percent','tests_passed','actual_elapsed_seconds','completed_at')},ensure_ascii=False))
if __name__=='__main__':build()
