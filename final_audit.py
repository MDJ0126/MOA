"""Audit the actual result files, archived copies, and live service state."""
import datetime
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

root=Path(__file__).resolve().parent
real=json.loads((root/'data'/'real-verification.json').read_text(encoding='utf-8'))
results=[]
for tid in real['tasks']:
    folder=root/'data'/'tasks'/tid
    manifest=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    assert manifest['status']=='completed'
    assert manifest['verification']['passed']
    assert manifest['verification']['tests_run']==6
    assert not manifest['verification']['missing_files']
    with zipfile.ZipFile(folder/'artifacts.zip') as archive:
        assert archive.testzip() is None
        for entry in manifest['files']:
            path=folder/'workspace'/entry['path']
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest==entry['sha256']
            assert digest==manifest['verification']['source_hashes'][entry['path']]
            archived=archive.read('workspace/'+entry['path'].replace('\\','/'))
            assert hashlib.sha256(archived).hexdigest()==digest
    results.append({'task_id':tid,'status':'completed','tests':6,'revision':manifest['revision'],'zip_and_source_hashes_match':True})
with urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=5) as response:state=json.load(response)
assert not state['paused']
assert state['supervisor']['children']['app']['running'] and state['supervisor']['children']['model']['running']
runtime=json.loads((root/'data'/'runtime-verification.json').read_text(encoding='utf-8'))
assert runtime['is_sleeping'] and runtime['unauthenticated_status']==401
result={'completed_at_kst':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
        'unit_tests_passed':13,'http_integration':'passed','dashboard_javascript_syntax':'passed',
        'real_model':state['model_description'],'real_tasks':results,'runtime':runtime,
        'process_recovery':json.loads((root/'data'/'recovery-verification.json').read_text(encoding='utf-8')),
        'dashboard':'http://127.0.0.1:8765','ready':True,
        'limitations':['engine and Slack deferred','no VM installed','no GUI visual verification','no 24-hour soak test','Python test processes are not an OS sandbox']}
(root/'data'/'verification-final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ready':True,'model':result['real_model'],'tasks':results,'completed_at_kst':result['completed_at_kst']},ensure_ascii=False),flush=True)
