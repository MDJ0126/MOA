"""Persistent task transitions; elapsed time never depends on browser uptime."""
import time

CLOCK="(julianday('now')-2440587.5)*86400.0"
TERMINAL={'completed','failed','cancelled','interrupted'}

def initialize(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS task_timing_events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,status TEXT,stage INTEGER,
          recorded REAL NOT NULL,source TEXT NOT NULL DEFAULT 'live');
        CREATE INDEX IF NOT EXISTS task_timing_task ON task_timing_events(task_id,seq);
        CREATE TRIGGER IF NOT EXISTS task_timing_created AFTER INSERT ON tasks BEGIN
          INSERT INTO task_timing_events(task_id,status,stage,recorded) VALUES(NEW.id,NEW.status,NEW.stage,COALESCE(NEW.created,"""+CLOCK+"""));
        END;
        CREATE TRIGGER IF NOT EXISTS task_timing_changed AFTER UPDATE OF status,stage ON tasks
        WHEN OLD.status IS NOT NEW.status OR OLD.stage IS NOT NEW.stage BEGIN
          INSERT INTO task_timing_events(task_id,status,stage,recorded) VALUES(NEW.id,NEW.status,NEW.stage,"""+CLOCK+""");
        END;
        """)
        c.execute("INSERT INTO task_timing_events(task_id,status,stage,recorded,source) SELECT id,status,stage,?,'partial' FROM tasks WHERE status IN ('running','queued') AND NOT EXISTS(SELECT 1 FROM task_timing_events e WHERE e.task_id=tasks.id)",(time.time(),))


def enrich(connection,tasks,now=None):
    if not tasks:return tasks
    now=time.time() if now is None else now
    ids=[t['id'] for t in tasks];marks=','.join('?' for _ in ids)
    grouped={tid:[] for tid in ids}
    for row in connection.execute('SELECT * FROM task_timing_events WHERE task_id IN ('+marks+') ORDER BY seq',ids):grouped[row['task_id']].append(dict(row))
    historical={r['task_id']:dict(r) for r in connection.execute("SELECT task_id,MIN(CASE WHEN kind='start' THEN created END) started,MAX(CASE WHEN kind IN ('start','handoff','error','control','progress','guest_result','research_result') THEN created END) finished FROM messages WHERE task_id IN ("+marks+") GROUP BY task_id",ids)}
    for task in tasks:
        events=grouped[task['id']];terminal=task['status'] in TERMINAL
        if not events:
            old=historical.get(task['id'],{});start=old.get('started');end=old.get('finished')
            task['timing']={'recorded':bool(start is not None and end is not None and end>start),'measured_at':now,'estimated':True,'started_at':start,'finished_at':end if terminal else None,'elapsed_seconds':max(0,(end if terminal else now)-start) if start is not None and end is not None else None,'active_seconds':None,'stages':[]}
            continue
        end=events[-1]['recorded'] if terminal else now
        active=0;stages={}
        for i,event in enumerate(events):
            stop=events[i+1]['recorded'] if i+1<len(events) else end
            if event['status']=='running':
                seconds=max(0,stop-event['recorded']);active+=seconds;stages[event['stage']]=stages.get(event['stage'],0)+seconds
        starts=[e['recorded'] for e in events if e['status']=='running']
        partial=events[0]['source']=='partial' or events[0]['status']!='queued'
        task['timing']={'recorded':True,'measured_at':now,'estimated':partial,'started_at':min(starts) if starts else None,'finished_at':end if terminal else None,'elapsed_seconds':max(0,end-(events[0]['recorded'] if partial else task['created'])),'active_seconds':active,'stages':[{'stage':stage,'seconds':seconds} for stage,seconds in sorted(stages.items())],'events':[{'status':e['status'],'stage':e['stage'],'at':e['recorded']} for e in events]}
    return tasks
