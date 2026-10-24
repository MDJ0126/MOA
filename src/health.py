"""Observed service state; no model, VM, or Slack network requests."""
import json
import os
import time


def status(store,worker,thread,started,instance):
    now=time.time()
    with store.connect() as c:
        counts={r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status')}
        paused=c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='true'
        active=[{'id':r['id'],'stage':r['stage']} for r in c.execute("SELECT id,stage FROM tasks WHERE status='running'")]
    record=None;path=store.root/'supervisor.json'
    try:
        if path.stat().st_size<=65536:
            value=json.loads(path.read_text(encoding='utf-8'))
            record={'pid':value['pid'],'record_age_seconds':max(0,now-float(value['updated'])),'restarts':value.get('restarts',{})}
    except (OSError,ValueError,TypeError,KeyError):pass
    return {'version':1,'observed_at':now,'app':{'pid':os.getpid(),'instance_id':instance,'started':started,'uptime_seconds':max(0,now-started)},
            'worker':{'thread_alive':thread.is_alive(),'stop_requested':worker.stop.is_set(),'last_loop_at':worker.last_loop_at,'heartbeat_write_ok':worker.heartbeat_write_ok},
            'queue':{'paused':paused,'counts':counts,'active':active},'supervisor_record':record,
            'scope':'HTTP 응답·스레드·저장 상태 관측입니다. 기록 신선도는 프로세스 생존이나 실제 작업 성공을 보장하지 않습니다.'}
