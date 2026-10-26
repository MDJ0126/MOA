import json,time
from pathlib import Path
from app import Store
from setup_config import team_settings
from leader_inbox import LeaderInbox
import leader_inbox
from unittest.mock import patch
R=Path.cwd();cfg=json.loads((R/'config.json').read_text(encoding='utf-8'));s=Store(R/'reports/role-presets-qa/data');agents=team_settings(s)['agents'];start=time.time()
print('Actual local model: routing research request to edited preset',flush=True)
original=leader_inbox.request_json
def observed(*args,**kwargs):
 response=original(*args,**kwargs);(R/'reports/role-presets-qa/model-response.json').write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding='utf-8');return response
with patch.object(leader_inbox,'request_json',side_effect=observed):result=LeaderInbox(s,cfg['model'],cfg['model_url']).decide({'title':'전담 리서처에게 공개 문서 조사와 출처 비교 보고서 작성을 맡겨 주세요. 코드 구현과 프로그램 설치는 필요 없습니다.','agents':agents})
(R/'reports/role-presets-qa/model-routing.json').write_text(json.dumps({'elapsed_seconds':round(time.time()-start,2),'decision':result},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False),flush=True)
assert result['work_kind']=='research'
assert next(a for a in result['assignments'] if a['role']=='developer')['agent_id']=='developer',result
print('PASS: real model chose research preset owner, not software department routing',flush=True)
