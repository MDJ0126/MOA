"""Durable completion notifications consumed by the Windows tray."""
import json
import time
import uuid
from setup_config import read_setting

def initialize(store):
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS desktop_notifications(
          id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT UNIQUE,title TEXT NOT NULL,
          kind TEXT DEFAULT 'completed',state TEXT DEFAULT 'pending',created REAL,
          claimed REAL,token TEXT DEFAULT '',sent REAL,error TEXT DEFAULT '');
        CREATE TRIGGER IF NOT EXISTS desktop_task_completed AFTER UPDATE OF status ON tasks
        WHEN NEW.status='completed' AND OLD.status!='completed'
          AND COALESCE(json_extract((SELECT value FROM settings WHERE key='desktop_notifications'),'$.enabled'),1)=1
        BEGIN
          INSERT OR IGNORE INTO desktop_notifications(task_id,title,created)
          VALUES(NEW.id,NEW.title,CAST(strftime('%s','now') AS REAL));
        END;
        ''')

def settings(store):
    return {'enabled':read_setting(store,'desktop_notifications',{'enabled':True}).get('enabled',True) is True}

def configure(store,data):
    if type(data.get('enabled')) is not bool:raise ValueError('알림 켜짐 여부를 확인하세요.')
    value={'enabled':data['enabled']}
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('desktop_notifications',json.dumps(value)))
        if not value['enabled']:c.execute("UPDATE desktop_notifications SET state='suppressed' WHERE state='pending'")
    return value

def test(store):
    with store.connect() as c:
        if not enabled_in(c):raise ValueError('완료 알림을 먼저 켜 주세요.')
        c.execute("INSERT INTO desktop_notifications(title,kind,created) VALUES(?,'test',?)",('MOA 테스트 알림입니다. 작업 완료 알림이 이곳에 표시됩니다.',time.time()))
    return {'queued':True}

def enabled_in(c):
    row=c.execute("SELECT value FROM settings WHERE key='desktop_notifications'").fetchone()
    return row is None or json.loads(row[0]).get('enabled',True) is True

def claim(store):
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if not enabled_in(c):return {'items':[]}
        rows=c.execute("SELECT id,task_id,title,kind FROM desktop_notifications WHERE state='pending' ORDER BY id LIMIT 5").fetchall()
        token=uuid.uuid4().hex
        for row in rows:c.execute("UPDATE desktop_notifications SET state='claimed',claimed=?,token=? WHERE id=? AND state='pending'",(time.time(),token,row['id']))
    # Claimed notifications are not automatically replayed after a tray crash:
    # a successful OS handoff cannot be distinguished from a lost acknowledgement.
    return {'items':[dict(r) for r in rows],'token':token}

def acknowledge(store,data):
    token=data.get('token');success=data.get('success')
    if not isinstance(token,str) or len(token)!=32 or type(success) is not bool:raise ValueError('알림 처리 기록을 확인하세요.')
    with store.connect() as c:c.execute("UPDATE desktop_notifications SET state=?,sent=?,error=? WHERE token=? AND state='claimed'",('sent' if success else 'failed',time.time(),'' if success else 'Windows 알림 요청 실패',token))
    return {'recorded':True}
