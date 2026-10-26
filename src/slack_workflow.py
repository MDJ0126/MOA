"""Persisted Slack handoffs: a worker never advances on an unconfirmed post."""
import json


def enabled(plan):
    return plan.get('collaboration', {}).get('transport') == 'slack'


def pending(store, tid):
    with store.connect() as c:
        task=c.execute('SELECT source_key FROM tasks WHERE id=?',(tid,)).fetchone()
        if task and task[0] and task[0].startswith('slack:'):
            if not c.execute('SELECT 1 FROM slack_events WHERE id=?',(task[0][6:],)).fetchone():return True
        return bool(c.execute("SELECT 1 FROM outbox o JOIN deliveries d ON d.id=o.delivery_id WHERE d.task_id=? AND o.state NOT IN ('sent','dismissed') LIMIT 1", (tid,)).fetchone())


def history(store, tid):
    with store.connect() as c:
        result=[]
        for r in c.execute('SELECT * FROM slack_turns WHERE task_id=? ORDER BY rowid',(tid,)):
            dm=c.execute('SELECT * FROM slack_direct WHERE delivery_id=?',(r['delivery_id'],)).fetchone()
            if dm and (not dm['received_event'] or not dm['images_ready']):continue
            content=json.loads(r['content'])
            if dm:content.update(received_images=json.loads(dm['received_images']),transport='slack_dm' if dm['channel'].startswith('D') else 'slack_channel',visual_understanding=False)
            result.append({'agent':r['sender'],'recipient':r['recipient'],'content':json.dumps(content,ensure_ascii=False),
                           'slack_ts':dm['ts'] if dm else r['ts'],'capability':''})
        return result


def handoff(result, verification, role, images=()):
    # These are shareable conclusions and evidence, never private model traces.
    return {'summary':result['summary'], 'steps':result.get('steps', []),
            'criteria':result.get('criteria', []), 'verification':verification,
            'capability':role, 'images':list(images),
            'guest_results':result.get('guest_results', [])}


def initialize(store):
    from slack_direct import initialize as initialize_direct
    initialize_direct(store)
    with store.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS slack_turns(delivery_id INTEGER PRIMARY KEY,task_id TEXT,sender TEXT,recipient TEXT,content TEXT,ts TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS slack_uploads(delivery_id INTEGER,path TEXT,file_id TEXT,state TEXT,error TEXT DEFAULT \'\',PRIMARY KEY(delivery_id,path))')
        c.execute('CREATE TABLE IF NOT EXISTS slack_attachments(event_id TEXT,file_id TEXT,task_id TEXT,path TEXT,mimetype TEXT,PRIMARY KEY(event_id,file_id))')


def record(store, row, payload, ts):
    with store.connect() as c:
        c.execute('INSERT OR IGNORE INTO slack_turns VALUES(?,?,?,?,?,?)',
                  (row['delivery_id'], payload['task_id'], payload['sender'], payload['recipient'],
                   json.dumps(payload['content'], ensure_ascii=False), ts))
