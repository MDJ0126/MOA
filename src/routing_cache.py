"""Exact-context reuse of a successful routing decision, never task results."""
import hashlib,json,time
from pathlib import Path

TTL_SECONDS=24*3600

def initialize(store):
    with store.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS routing_cache(key TEXT PRIMARY KEY,decision TEXT NOT NULL,origin_task TEXT NOT NULL,created REAL NOT NULL)')

def key(payload,model,url):
    if payload.get('conversation') or payload.get('recent_conversation') or payload.get('parent_request_id'):return None
    root=Path(__file__).resolve().parents[1]
    signature={'payload':payload,'model':model,'url':url,'version':1,
               'routing_source':hashlib.sha256((root/'src/leader_inbox.py').read_bytes()).hexdigest(),
               'config':(root/'config.json').read_text(encoding='utf-8')}
    return hashlib.sha256(json.dumps(signature,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def get(store,cache_key):
    if not cache_key:return None
    with store.connect() as c:
        row=c.execute("SELECT r.decision,r.origin_task,t.verification FROM routing_cache r JOIN tasks t ON t.id=r.origin_task WHERE r.key=? AND r.created>? AND t.status='completed'",(cache_key,time.time()-TTL_SECONDS)).fetchone()
    if not row:return None
    try:
        if json.loads(row['verification'] or '{}').get('passed') is not True:return None
        decision=json.loads(row['decision'])
        if decision.get('action')!='delegate' or not decision.get('refinement'):return None
        return decision,row['origin_task']
    except (ValueError,TypeError):return None

def put(store,cache_key,decision,task_id):
    if not cache_key or decision.get('action')!='delegate' or not decision.get('refinement'):return
    value={k:v for k,v in decision.items() if k!='routing_cache'}
    with store.connect() as c:c.execute('INSERT INTO routing_cache VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET decision=excluded.decision,origin_task=excluded.origin_task,created=excluded.created',(cache_key,json.dumps(value,ensure_ascii=False),task_id,time.time()))
