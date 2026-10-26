"""Carry a verified parent result into a goal correction, without editing it."""
import hashlib,json,os,re,shlex,tarfile,tempfile,time
from pathlib import Path,PurePosixPath
from reports import workspace_files,hash_file

def seed(store,task,plan,files,vm,role):
    request=plan.get('leader_workflow',{}).get('request_id')
    if not request:return
    with store.connect() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='goal_items'").fetchone():return
        parent=c.execute('SELECT p.task_id FROM goal_items i JOIN goal_items p ON i.parent_item=p.id WHERE i.request_id=?',(request,)).fetchone()
        if not parent:return
        source=parent['task_id']
        old=c.execute('SELECT status,verification,execution_plan FROM tasks WHERE id=?',(source,)).fetchone()
    if not re.fullmatch('[a-f0-9]{12}',source or '') or not old or old['status']!='completed':raise ValueError('수정할 원본 작업의 완료 상태를 확인하지 못했습니다.')
    receipt=store.root/'tasks'/task['id']/'goal-seed.json'
    if receipt.exists():
        if receipt.is_symlink() or receipt.stat().st_nlink!=1:raise ValueError('수정 원본 기록의 링크를 허용하지 않습니다.')
        if json.loads(receipt.read_text(encoding='utf-8')).get('source')!=source:raise ValueError('수정 원본 기록이 달라졌습니다.')
        return
    proof=json.loads(old['verification'] or '{}');previous=json.loads(old['execution_plan'] or '{}')
    origin=store.root/'tasks'/source/'workspace';paths=workspace_files(origin)
    hashes={p.relative_to(origin).as_posix():hash_file(p) for p in paths}
    if proof.get('passed') is not True or not proof.get('source_hashes') or proof['source_hashes']!=hashes:raise ValueError('수정 원본이 검증 후 변경되었거나 파일 해시를 확인하지 못했습니다.')
    before=previous.get('environment',{});after=plan.get('environment',{})
    if before.get('kind','local')!=after.get('kind','local'):raise ValueError('수정 원본과 실행 환경이 다릅니다. 원본 환경에서 다시 요청하세요.')
    if len(paths)>512 or sum(p.stat().st_size for p in paths)>32000000:raise ValueError('수정 원본이 자동 복사 한도를 넘었습니다.')
    if after.get('guest_execution'):
        if any(before.get('config',{}).get(k)!=after.get('config',{}).get(k) for k in ('guest_os','disk')):raise ValueError('수정 원본의 VM 운영체제·디스크가 달라졌습니다.')
        archive=origin/'guest-workspace.tar.gz'
        if not archive.is_file():raise ValueError('수정 원본의 VM 파일 기록이 없습니다.')
        with tarfile.open(archive,'r:gz') as tar:
            members=tar.getmembers()
            if len(members)>512 or sum(m.size for m in members)>16000000:raise ValueError('VM 수정 원본 크기 제한')
            for m in members:
                name=m.name;parts=PurePosixPath(name).parts
                if not m.isfile() or not parts or name.startswith('/') or '..' in parts or ':' in name or '\\' in name:raise ValueError('VM 수정 원본 경로 오류')
                digest=hashlib.sha256(tar.extractfile(m).read()).hexdigest()
                if after['config']['guest_os']=='windows':
                    src=('C:\\MOA\\tasks\\'+source+'\\'+name.replace('/','\\')).replace("'","''");dst=name.replace('/','\\').replace("'","''")
                    command="function GetMoaSeedHash([string]$p){$a=[Security.Cryptography.SHA256]::Create();try{[BitConverter]::ToString($a.ComputeHash([IO.File]::ReadAllBytes($p))).Replace('-','').ToLower()}finally{$a.Dispose()}};"+f"$s='{src}';$d=Join-Path (Get-Location).Path '{dst}';if((GetMoaSeedHash $s) -ne '{digest}'){{throw 'Source changed'}};if(Test-Path -LiteralPath $d){{if((GetMoaSeedHash $d) -ne '{digest}'){{throw 'Destination exists'}}}}else{{New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($d))|Out-Null;Copy-Item -LiteralPath $s -Destination $d}}"
                else:
                    if not name.isascii():raise ValueError('Linux VM 수정 복사는 ASCII 경로만 지원합니다.')
                    src=shlex.quote('/home/tc/moa/'+source+'/'+name);dst=shlex.quote(name);directory=shlex.quote(str(PurePosixPath(name).parent))
                    command=f"test \"$(sha256sum {src} | cut -d' ' -f1)\" = {digest} && mkdir -p {directory} && (if test -e {dst}; then test \"$(sha256sum {dst} | cut -d' ' -f1)\" = {digest}; else cp {src} {dst}; fi)"
                result=vm.command(task['id'],role,command)
                if result['exit_code']!=0:raise ValueError('VM 수정 원본 복사·해시 검증 실패: '+name)
    else:
        for p in paths:
            dest=files.path(p.relative_to(origin).as_posix())
            if dest.exists():
                if dest.is_symlink() or dest.stat().st_nlink!=1 or hash_file(dest)!=hashes[p.relative_to(origin).as_posix()]:raise ValueError('수정 작업 폴더의 기존 파일이 원본과 다릅니다.')
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                data=p.read_bytes()
                if hashlib.sha256(data).hexdigest()!=hashes[p.relative_to(origin).as_posix()]:raise ValueError('수정 원본이 복사 중 변경되었습니다.')
                with dest.open('xb') as out:out.write(data)
            if hash_file(dest)!=hashes[p.relative_to(origin).as_posix()]:raise ValueError('수정 원본 복사 후 해시 불일치')
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=receipt.parent,delete=False,suffix='.tmp') as out:
        json.dump({'source':source,'hashes':hashes,'created':time.time()},out);out.flush();os.fsync(out.fileno());temp=out.name
    os.replace(temp,receipt)
    store.log(task['id'],'system','progress','검증된 이전 결과를 복사했습니다. 원본은 보존하고 요청한 부분을 수정합니다. 원본 작업: '+source)
