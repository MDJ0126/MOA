"""Independent artifact/VM-command/Slack-byte audit of measured real tasks."""
import argparse,hashlib,io,json,re,sqlite3,tarfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/__import__('os').environ.get('MOA_BENCH_OUT','reports/full-performance-20261025')
EXPECTED=b'echo $(($1 + $2))\n'
def digest(data):return hashlib.sha256(data).hexdigest()
def audit(phase):
    record=json.loads((OUT/(phase+'.json')).read_text(encoding='utf-8'));tid=record['task_id']
    bundle=(OUT/(phase+'-artifacts.zip')).read_bytes()
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        nested=next(n for n in z.namelist() if n.endswith('guest-workspace.tar.gz'))
        with tarfile.open(fileobj=io.BytesIO(z.read(nested)),mode='r:gz') as tar:
            member=next(m for m in tar if m.name.lstrip('./')=='add.sh');source=tar.extractfile(member).read()
    c=sqlite3.connect('file:'+str(ROOT/'data/state.sqlite3').replace('\\','/')+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone());plan=json.loads(task['execution_plan'])
    verification=json.loads(task['verification']);reviewer=plan['assignment']['reviewer']
    review=[]
    for event in record['events']:
        if event['kind']=='guest_result' and event['agent']==reviewer:review.append(json.loads(event['content']))
    commands='\n'.join(x.get('command','') for x in review)
    assertions={label:bool(re.search(pattern,commands)) for label,pattern in (
        ('positive',r'sh\s+add\.sh\s+2\s+3'),('negative',r'sh\s+add\.sh\s+-2\s+1'),('zero',r'sh\s+add\.sh\s+0\s+0'))}
    marker=any(x.get('exit_code')==0 and 'REVIEW_PASS' in x.get('output','').splitlines() for x in review)
    independent_ok=bool(review) and all(assertions.values()) and marker and all(x.get('exit_code')==0 for x in review)
    images=[]
    for row in c.execute('SELECT u.path original,a.path received,a.file_id FROM slack_uploads u JOIN slack_attachments a ON a.file_id=u.file_id WHERE a.task_id=?',(tid,)):
        original=row['original'].split('|',1)[-1];received=row['received']
        folder=ROOT/'data/tasks'/tid;original_path=folder/original;received_path=folder/received
        if not original_path.resolve().is_relative_to(folder.resolve()) or not received_path.resolve().is_relative_to(folder.resolve()):raise ValueError('Unexpected image path')
        left=original_path.read_bytes();right=received_path.read_bytes()
        images.append({'file_id':row['file_id'],'original':original,'received':received,'sha256':digest(left),'received_sha256':digest(right),'same_bytes':left==right,'png_signature':left.startswith(b'\x89PNG\r\n\x1a\n')})
    checks={'exact_exported_bytes':source==EXPECTED,'guest_verification_passed':verification.get('passed') is True,
        'independent_actors':plan['assignment']['developer']!=reviewer,'all_requested_reviewer_assertions_executed':independent_ok,
        'all_slack_deliveries_sent':bool(record['deliveries']) and all(d['state']=='sent' and d['receipt'] for d in record['deliveries']),
        'recipient_events_observed':bool(record['receipts']) and all(r['received_event'] and r['images_ready'] for r in record['receipts']),
        'received_images_match_original':bool(images) and all(x['same_bytes'] and x['png_signature'] for x in images)}
    metrics=[json.loads(e['content']) for e in record['events'] if e['kind']=='model_metrics']
    result={'phase':phase,'task_id':tid,'elapsed_seconds':record['elapsed_seconds'],'leader_seconds':record['leader_seconds'],'checks':checks,
        'quality_passed':sum(bool(v) for v in checks.values()),'quality_total':len(checks),'exported_source_sha256':digest(source),
        'reviewer_commands':review,'requested_assertions_observed':assertions,'review_pass_marker_observed':marker,
        'slack_deliveries':len(record['deliveries']),'slack_recipient_receipts':len(record['receipts']),'images':images,
        'guest_model_calls':len(metrics),'guest_model_wall_seconds':round(sum(m['elapsed_seconds'] for m in metrics),3),
        'guest_prompt_tokens':sum(m.get('usage',{}).get('prompt_tokens',0) for m in metrics),
        'guest_uncached_prompt_tokens':sum(m.get('timings',{}).get('prompt_n',0) for m in metrics),
        'guest_server_compute_seconds':round(sum((m.get('timings',{}).get('prompt_ms',0)+m.get('timings',{}).get('predicted_ms',0))/1000 for m in metrics),3)}
    (OUT/(phase+'-audit.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('images','reviewer_commands')},ensure_ascii=False,indent=2))
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['before','after'],required=True);audit(p.parse_args().phase)
