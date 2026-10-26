"""Actual QEMU guest file creation/readback. No Slack credentials or fake results."""
from pathlib import Path
import json
import sys
import time
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from app import Store,LocalFiles
import tarfile
from vm_runtime import VM


def main():
    store=Store(ROOT/'data');vm=VM(store)
    with store.connect() as c:
        if c.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():raise RuntimeError('기존 진행·대기 작업이 있어 독립 검증을 보류합니다.')
        paused=c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='true'
    # Starting before publishing a task preserves the queue on resource failure.
    vm.start()
    store.control('pause')
    tid=store.add('실제 게스트 명령: 파일 생성 후 다른 역할에서 내용을 읽고 종료 코드를 검증합니다.','llama',environment='guest',auto_recover=False)
    with store.connect() as c:c.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
    store.control('resume')
    started=time.time();results=[]
    try:
        with store.connect() as c:task=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        for role,actor,command in [('developer','builder',"printf 'MOA_GUEST_OK\\n' > ready.txt; cat ready.txt"),
                                   ('reviewer','checker',"test \"$(cat ready.txt)\" = MOA_GUEST_OK && printf 'REVIEW_PASS\\n'")]:
            with vm.stage(task,role,actor):
                result=vm.command(tid,role,command)
                results.append(result);store.log(tid,actor,'guest_result',json.dumps(result))
                assert result['exit_code']==0,result
                if role=='reviewer':
                    exported=vm.export_workspace(tid,role,LocalFiles(store.root/'tasks'/tid/'workspace'))
                    with tarfile.open(store.root/'tasks'/tid/'workspace'/exported['path'],'r:gz') as bundle:
                        member=next(item for item in bundle if item.name.lstrip('./')=='ready.txt')
                        assert bundle.extractfile(member).read()==b'MOA_GUEST_OK\n'
        assert 'MOA_GUEST_OK' in results[0]['output'] and 'REVIEW_PASS' in results[1]['output']
        with store.connect() as c:c.execute("UPDATE tasks SET status='completed',stage=3 WHERE id=?",(tid,))
        record={'task_id':tid,'status':'completed','elapsed_seconds':round(time.time()-started,2),'results':results,'exported_archive_verified':True,
                'evidence':[str(p.relative_to(ROOT/'data/tasks'/tid)) for p in (ROOT/'data/tasks'/tid/'vm').glob('*.png')],
                'scope':'Actual QEMU guest file writes, terminal stdout and exit codes; no Slack network or model inference in this probe.'}
        (ROOT/'data/guest-live-verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(record,ensure_ascii=False),flush=True)
    except Exception:
        with store.connect() as c:c.execute("UPDATE tasks SET status='interrupted',error='게스트 실검증 중단: 재실행 전 게스트 상태 확인 필요' WHERE id=?",(tid,))
        raise
    finally:
        if paused:store.control('pause')


if __name__=='__main__':main()
