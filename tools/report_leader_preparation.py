"""Compare audited whole workflows under the same current review policy."""
import datetime,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/leader-preparation-20261025'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def main():
 before,after=read('before-audit.json'),read('after-audit.json')
 rb,ra=read('before.json'),read('after.json')
 for metric,record in ((before,rb),(after,ra)):
  metric['model_preparation_seconds']=record['model_preparation_seconds'];metric['vm_preparation_seconds']=record['vm_preparation_seconds']
 eb,ea=read('before-environment.json'),read('after-environment.json')
 assert all(before['checks'].values()) and all(after['checks'].values())
 assert before['current_policy_approved'] and after['current_policy_approved']
 assert rb['title']==ra['title']
 assert before['exported_source_sha256']==after['exported_source_sha256']
 assert eb['source']==ea['source'],'Source changed between measured phases'
 assert eb['health']['source_status']['running_id']==ea['health']['source_status']['running_id']
 assert after['routing_cache']['hit'] is True,'Exact-context routing cache did not hit'
 c=sqlite3.connect('file:'+str(ROOT/'data/state.sqlite3').replace('\\','/')+'?mode=ro',uri=True)
 plans=[]
 for record in (rb,ra):plans.append(json.loads(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(record['task_id'],)).fetchone()[0]))
 for field in ('environment','workspace_context','assignment','leader_review'):assert plans[0].get(field)==plans[1].get(field),field
 session=read('session.json');now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)));start=datetime.datetime.fromisoformat(session['started_kst'])
 reduction=round((1-after['including_preparation_seconds']/before['including_preparation_seconds'])*100,2)
 result={'before':before,'after':after,'inclusive_reduction_percent':reduction,'same_request':True,'same_source':True,'same_vm_workspace_and_review_policy':True,'reference_401':read('reference-401/before-audit.json'),'session':session,'completed_kst':now.isoformat(),'actual_seconds':round((now-start).total_seconds(),3),'scope':'Same current policy, including actual independent rechecks and at least two leader approvals. End-to-end includes app restart/Slack readiness, priority transition, VM/model readiness, task and ZIP readback. Equal 75-second idle interval is the separate load condition. Routing cache reuses only a verified exact-context routing decision; all task evidence is fresh.'}
 (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 text='# 리더 배정·모델 준비 최적화 전후 실측\n\n'
 text+='동일 요청·소스·VM 설정·작업 지침·현재 검토 정책을 대조했습니다. 양쪽 모두 리더 최소 2회 검토와 실제 재검증을 유지합니다. 직전 401.024초는 추가 검토 정책 전 기록이므로 개선율 계산에 섞지 않았습니다.\n\n'
 text+='| 항목 | 최적화 끔 | 최적화 켬 |\n|---|---:|---:|\n'
 for label,key in [('준비 포함 전체(초)','including_preparation_seconds'),('준비 전체(초)','preparation_seconds'),('모델 준비(초)','model_preparation_seconds'),('VM 준비(초)','vm_preparation_seconds'),('앱 재시작·Slack 연결(초)','app_restart_seconds'),('작업 구간(초)','elapsed_seconds'),('리더 배정(초)','leader_seconds'),('담당 모델 호출(리더 검토 제외)','guest_model_calls'),('Slack 전달','slack_deliveries')]:text+=f'| {label} | {before[key]} | {after[key]} |\n'
 text+=f"| 결과 품질 | {before['quality_passed']}/{before['quality_total']} | {after['quality_passed']}/{after['quality_total']} |\n"
 text+=f"| 실제 리더 검토 | {len(before['leader_reviews'])}회·최종승인 | {len(after['leader_reviews'])}회·최종승인 |\n\n준비 포함 단축률: {reduction}%.\n\n"
 text+='배정 재사용은 요청·팀·지침·모델 설정이 같은 반복 요청에서만 적용됩니다. 신규 요청은 실제 모델 배정을 거칩니다. 결과물·실행 결과·리더 최종 검토는 캐시하지 않습니다. 모델은 기존 7B CPU 설정을 유지했습니다. 단일 작업 실측이며 일반 작업 전체의 품질·속도를 보장하는 수치는 아닙니다.\n\n'
 text+='백그라운드 평가는 60초 유휴 대기를 적용하고, 새 사용자 작업이나 일시정지가 감지되면 로컬 모델 연결을 종료해 평가를 재대기시킵니다. 점수와 평가를 임의로 생성하지 않습니다. 반복 검토 캡처는 별도 파일로 보존합니다.\n\n'
 text+='모델 준비 시 변경 전에는 평가가 없었고 변경 후에는 실행 중인 평가가 있었습니다. 취소 요청 뒤 슬롯 해제까지 실제 로그에서 56.103초가 걸렸습니다. 따라서 모델 준비 개선은 달성하지 못했습니다. 동일 소스·모델·VM·요청·검토 정책이지만 이 부하 차이가 있으며, 전체 단축률은 이 한 쌍의 관측값입니다.\n\n'
 text+='추가 실험에서 평가 유예를 300초로 늘리자 모델 준비는 1.952초, 배정은 1.043초였지만 최종 리더 검토가 실패했습니다. 이를 품질 유지 성공으로 인정하지 않고 검증된 60초 설정으로 되돌렸습니다. failed-idle300 폴더에 실패를 보존했으며, 설정 변경과 검토 실패의 인과관계는 미확인입니다. 이미 시작된 평가의 취소 지연은 남아 있습니다.\n\n'
 text+='재시작·Slack 준비부터 우선 전환·VM/모델 준비·전체 작업·최종 ZIP 읽기까지 합산했습니다. 양쪽의 동일한 75초 유휴 구간은 부하 조건이며 합계에서 제외했습니다. 제외된 VM 시작 실패와 외부 QA 경합 시도는 excluded-* 폴더에 보존했습니다. 사용자가 선택한 QA 자연 종료 대기 시간은 아래 실제 작업 소요에는 포함됩니다.\n\n'
 text+='측정 후 다른 개발 변경으로 현재 디스크 소스와 실행본의 지문 차이가 확인됐습니다. 검증된 실행본을 유지했으며, 그 후속 변경까지 전체 실행 검증했다고 보고하지 않습니다. 배정 캐시·평가 대기·캡처 보존 코드는 현재 소스에도 남아 있음을 확인했습니다.\n\n'
 text+=f"제안 시간: 15~25분(이후 지연을 보고하고 수정). 실제 소요: {result['actual_seconds']}초. 완료: {result['completed_kst']}.\n\nXERJ: 미사용 — 제공 도구가 없어 rg와 현재 소스·실행 결과로 확인했습니다.\n"
 (OUT/'comparison.md').write_text(text,encoding='utf-8')
 print(json.dumps({k:v for k,v in result.items() if k not in ('before','after','reference_401','session')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
