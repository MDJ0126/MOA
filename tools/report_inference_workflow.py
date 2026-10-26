"""Summarize only audited, uncontaminated real workflow runs."""
import datetime,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/inference-performance-20261025'
def main():
    before=json.loads((OUT/'before-audit.json').read_text(encoding='utf-8'))
    after=json.loads((OUT/'after-audit.json').read_text(encoding='utf-8'))
    rb=json.loads((OUT/'before.json').read_text(encoding='utf-8'))
    ra=json.loads((OUT/'after.json').read_text(encoding='utf-8'))
    assert rb['title']==ra['title']
    assert all(before['checks'].values()) and all(after['checks'].values())
    assert before['exported_source_sha256']==after['exported_source_sha256']
    before['preparation_seconds']=round(rb.get('vm_preparation_seconds',0)+rb.get('model_preparation_seconds',0),3)
    after['preparation_seconds']=round(ra.get('vm_preparation_seconds',0)+ra.get('model_preparation_seconds',0),3)
    for metric in (before,after):metric['including_preparation_seconds']=round(metric['elapsed_seconds']+metric['preparation_seconds'],3)
    now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    start=datetime.datetime.fromisoformat('2026-10-25T14:50:07.7500916+09:00')
    result={'before_task':before['task_id'],'after_task':after['task_id'],'same_request':True,'same_exported_source':True,'before':before,'after':after,'elapsed_reduction_percent':round((1-after['elapsed_seconds']/before['elapsed_seconds'])*100,2),'proposed_minutes':[15,25],'revised_minutes':[28,32],'started_kst':start.isoformat(),'completed_kst':now.isoformat(),'actual_seconds':round((now-start).total_seconds(),3),'scope':'Prior measured baseline vs new production run; identical original request, 7B CPU model, VM/Slack, independent assertions, ZIP and received image bytes. VM/model preparation excluded in both. Single workload; not a general quality guarantee.','excluded_attempt':'excluded-queue-attempt/after.json includes unrelated work waiting; never used for speed comparison.'}
    result['including_preparation_reduction_percent']=round((1-after['including_preparation_seconds']/before['including_preparation_seconds'])*100,2)
    result['source_equivalence_evidence']='source-equivalence.json'
    (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[('전체 작업(초)','elapsed_seconds'),('별도 준비(초)','preparation_seconds'),('준비 포함 합계(초)','including_preparation_seconds'),('리더 배정(초)','leader_seconds'),('작업 모델 호출(리더 제외)','guest_model_calls'),('작업 모델 대기 합계(초)','guest_model_wall_seconds'),('작업 입력 토큰','guest_prompt_tokens'),('캐시 제외 입력 토큰','guest_uncached_prompt_tokens'),('Slack 전달','slack_deliveries'),('Slack 수신 확인','slack_recipient_receipts')]
    text='# 실제 VM·Slack 전체 작업 비교\n\n| 항목 | 변경 전 | 최종 변경 후 |\n|---|---:|---:|\n'
    text+=''.join(f'| {label} | {before[key]} | {after[key]} |\n' for label,key in rows)
    text+=f"| 품질 검사 | {before['quality_passed']}/{before['quality_total']} | {after['quality_passed']}/{after['quality_total']} |\n\n전체 시간 단축: {result['elapsed_reduction_percent']}%.\n\n"
    text+='직전 실측 기준과 동일 요청을 비교했습니다. VM과 모델 준비 시간은 양쪽 모두 별도입니다. 단일 셸 코드 작업의 결과이며 다른 업무의 속도나 품질을 보장하지 않습니다. 원문 파일 바이트, 다른 담당자의 양수·음수·0 실제 실행, Slack 전달·수신 및 이미지 원본/수신 바이트를 대조했습니다.\n\n'
    text+='적용: 개발/검토 역할 분리, 명시적 검증 명령의 원문 일괄 실행, 실제 실행 후 모델 판정 유지, 정확히 중복된 원문 입력 제거, 리더 배정 설명 생성량 축소. 범위를 벗어나는 명령은 기존 모델 경로를 유지합니다.\n\n'
    text+='측정 말미 다른 세션이 리서치 오류 복구 메시지를 수정하고 앱을 재시작·재개했습니다. 그 변경 한 곳만 역변환하면 측정 시작 소스의 SHA-256과 정확히 일치합니다(source-equivalence.json). 코드 작업 경로에는 변경이 없으며, 최종 일시정지 상태가 보존됐다고 주장하지 않습니다.\n\n'
    text+='중간 실측은 intermediate-before-leader-compaction/에 보존했습니다. 대기가 섞인 시도는 excluded-queue-attempt/에 보존하고 개선율에서 제외했습니다.\n\n'
    text+=f"제안 시간: 15~25분(이후 28~32분으로 수정). 실제 소요: {result['actual_seconds']}초. 완료: {result['completed_kst']}.\n\nXERJ: 미사용 — 제공된 도구가 없어 rg와 현재 소스·실행 결과로 검증.\n"
    (OUT/'comparison.md').write_text(text,encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k not in ('before','after')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
