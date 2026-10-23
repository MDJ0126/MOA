"""Extension contracts. No engine or Slack credentials are configured."""
from typing import Protocol

class EngineAdapter(Protocol):
    def inspect(self, project: str) -> dict: ...
    def execute(self, project: str, command: str, arguments: dict) -> dict: ...

class MessageAdapter(Protocol):
    def send(self, event_id: int, payload: dict) -> str: ...

class DisabledEngine:
    def inspect(self, project):
        return {'available':False,'reason':'게임 엔진 어댑터 미설정','project':project}
    def execute(self, project, command, arguments):
        raise RuntimeError('엔진 어댑터를 연결해야 실행할 수 있습니다.')

def flush_outbox(store, adapter: MessageAdapter):
    """Adapters must use event_id for idempotency; never rerun completed dev work."""
    import json
    import time
    with store.connect() as c:
        rows=c.execute("SELECT * FROM outbox WHERE state='pending' AND available_at<=? ORDER BY id",(time.time(),)).fetchall()
    for row in rows:
        try:
            receipt=adapter.send(row['id'],json.loads(row['payload']))
            with store.connect() as c:
                c.execute("UPDATE outbox SET state='sent',receipt=? WHERE id=?",(str(receipt),row['id']))
        except Exception as e:
            with store.connect() as c:
                c.execute('UPDATE outbox SET attempts=attempts+1,error=?,available_at=? WHERE id=?',
                          (str(e),time.time()+min(300,5*2**min(row['attempts'],6)),row['id']))
