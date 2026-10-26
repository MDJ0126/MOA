"""Offline data backup and restore. Never starts or controls a VM."""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import re
import sqlite3
import stat
import tempfile
import time
import zipfile
from supervisor import acquire, write_status
from setup_config import archive_setting

SKIP = {'worker.lock', 'supervisor.lock', 'supervisor.stop', 'supervisor.json', 'model.key','state.sqlite3-wal','state.sqlite3-shm'}
CHUNK_SIZE=64*1024

def publish_restored(staged,destination):
    """Bound Windows sharing failures without replacing an existing destination."""
    delays=(.05,.15,.3)
    for attempt in range(len(delays)+1):
        if os.path.lexists(destination):raise ValueError('복원 대상이 이미 생성되었습니다.')
        try:
            staged.rename(destination)
            return
        except OSError as error:
            if os.name!='nt' or getattr(error,'winerror',None) not in (5,32,33) or attempt==len(delays):raise
            time.sleep(delays[attempt])

class BusyBackup(ValueError):pass


def _inside(path, parent):
    return path == parent or parent in path.parents

def manifest_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('백업 목록의 JSON 키가 중복됩니다.')
        value[key]=item
    return value

def checked_backup_path(name,aliases):
    parts=PurePosixPath(name).parts
    reserved={'CON','PRN','AUX','NUL'}|{f'{prefix}{i}' for prefix in ('COM','LPT') for i in range(1,10)}
    if not parts or PurePosixPath(name).as_posix()!=name or any(p in ('.','..') or p.endswith((' ','.')) or p.split('.')[0].rstrip(' ').upper() in reserved for p in parts) or any(c in '<>:"|?*' or ord(c)<32 for c in name) or '\\' in name or name.startswith('/') or name.casefold() in SKIP or parts[0].casefold()=='vm' or 'vbox-private' in tuple(p.casefold() for p in parts):
        raise ValueError('잘못된 백업 경로입니다.')
    for index in range(1,len(parts)+1):
        prefix='/'.join(parts[:index]);folded=prefix.casefold()
        if folded in aliases and aliases[folded]!=prefix:raise ValueError('대소문자 디렉터리 경로가 충돌합니다.')
        aliases[folded]=prefix
    return parts

def restored_connection(key,raw):
    """Normalize inactive connection metadata without reading keys or VM files."""
    from vm_runtime import DEFAULT as VM_DEFAULT
    default={**VM_DEFAULT,'updated':0} if key=='vm' else {'enabled':False,'connections':[],'allowed_users':[],'channel':'','receive':True,'notify':True,'updated':0}
    try:
        source=json.loads(raw,object_pairs_hook=manifest_object)
        if not isinstance(source,dict):raise ValueError('connection object required')
        value={name:source.get(name,item) for name,item in default.items()}
        if any(type(value[name]) is not type(item) for name,item in default.items() if name!='updated'):raise ValueError('connection field type')
        if type(value['updated']) not in (int,float) or not math.isfinite(value['updated']) or value['updated']<0:raise ValueError('connection revision')
        if key=='vm':
            if not 256<=value['memory_mb']<=4096 or not 1<=value['cpus']<=2 or value['accelerator'] not in ('tcg','whpx'):raise ValueError('VM limits')
            for name in ('iso','disk'):
                text=value[name];path=PurePosixPath(text.replace('\\','/'))
                if not text and name=='disk':continue
                if not text or path.is_absolute() or '..' in path.parts or ':' in text or ',' in text or path.suffix.lower() not in (('.iso',) if name=='iso' else ('.qcow2','.img')):raise ValueError('VM path')
        else:
            if value['channel'] and not re.fullmatch('[CG][A-Z0-9]{5,}',value['channel']):raise ValueError('Slack channel')
            if any(not isinstance(user,str) or not re.fullmatch('[UW][A-Z0-9]{5,}',user) for user in value['allowed_users']):raise ValueError('Slack users')
            if len(value['connections'])>9:raise ValueError('Slack connection count')
            connections=[];ids=set();agents=set()
            for item in value['connections']:
                if not isinstance(item,dict):raise ValueError('Slack connection object')
                cid=item.get('id');agent=item.get('agent_id','shared')
                if not isinstance(cid,str) or not re.fullmatch('[a-z][a-z0-9_-]{0,31}',cid) or cid in ids or not isinstance(agent,str) or not re.fullmatch('[a-z][a-z0-9_-]{0,31}',agent) or agent in agents:raise ValueError('Slack connection identity')
                ids.add(cid);agents.add(agent);connections.append({'id':cid,'agent_id':agent})
            value['connections']=connections
        value['enabled']=False
        return value
    except (ValueError,TypeError,OverflowError,RecursionError):return default


def restored_execution_profile(raw):
    from environment_profiles import DEFAULT,validate
    try:
        source=json.loads(raw,object_pairs_hook=manifest_object)
        value=validate(source);revision=source.get('updated',0)
        if type(revision) not in (int,float) or not math.isfinite(revision) or revision<0:raise ValueError('profile revision')
        return {**value,'updated':revision}
    except (ValueError,TypeError,OverflowError,RecursionError):return {**DEFAULT,'updated':0}


def backup(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not (source / 'state.sqlite3').is_file():
        raise ValueError('백업할 데이터베이스가 없습니다.')
    if output.exists() or _inside(output, source):
        raise ValueError('백업은 data 밖의 새 파일로 저장하세요.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        try:
            for name in ('supervisor.lock', 'worker.lock'):
                stack.enter_context(acquire(source / name))
        except OSError as exc:
            raise BusyBackup('앱과 감시 관리자를 정상 종료한 뒤 백업하세요.') from exc
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            archive = Path(temporary) / 'backup.zip'
            snapshot=Path(temporary)/'state.sqlite3'
            database=source/'state.sqlite3'
            if database.is_symlink() or database.stat().st_nlink!=1:raise ValueError('링크 데이터베이스는 백업하지 않습니다.')
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(snapshot)) as target:
                original.backup(target)
                target.execute('PRAGMA journal_mode=DELETE')
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('백업 데이터베이스가 손상되었습니다.')
            files = {};aliases={}
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                for path in sorted(source.rglob('*')):
                    relative = path.relative_to(source)
                    if relative.parts[0].casefold() in ('vm','slack-auto') or 'vbox-private' in tuple(p.casefold() for p in relative.parts) or relative.as_posix().casefold() in SKIP:
                        continue
                    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                        raise ValueError('링크가 포함된 데이터는 백업하지 않습니다.')
                    if not _inside(path.resolve(), source):
                        raise ValueError('데이터 폴더 밖 경로입니다.')
                    if path.is_file():
                        if path.stat(follow_symlinks=False).st_nlink!=1:raise ValueError('하드링크가 포함된 데이터는 백업하지 않습니다.')
                        name='state.sqlite3' if relative.as_posix().casefold()=='state.sqlite3' else relative.as_posix()
                        if name in files:raise ValueError('중복 백업 경로입니다.')
                        checked_backup_path(name,aliases)
                        actual=snapshot if name=='state.sqlite3' else path
                        digest=hashlib.sha256();size=0
                        initial=actual.lstat()
                        valid=lambda info:stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not getattr(info,'st_file_attributes',0)&0x400
                        if not valid(initial):raise ValueError('백업 파일이 검사 중 변경되었습니다.')
                        with actual.open('rb') as stream:
                            opened=os.fstat(stream.fileno());current=actual.lstat()
                            if not valid(opened) or not valid(current) or (initial.st_dev,initial.st_ino)!=(opened.st_dev,opened.st_ino) or (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):
                                raise ValueError('백업 파일이 읽기 시작 중 변경되었습니다.')
                            with z.open('data/'+name,'w',force_zip64=True) as stored:
                                while block:=stream.read(CHUNK_SIZE):stored.write(block);digest.update(block);size+=len(block)
                            finished=os.fstat(stream.fileno());current=actual.lstat()
                            # Windows path and handle ctime can describe different
                            # timestamps; compare content metadata shared by both.
                            signature=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
                            if not valid(finished) or not valid(current) or size!=initial.st_size or any(signature(info)!=signature(initial) for info in (opened,finished,current)):
                                raise ValueError('백업 파일 내용이 읽기 중 변경되었습니다. 파일 저장을 마친 뒤 다시 백업하세요.')
                        files[name] = {'size':size,'sha256':digest.hexdigest()}
                z.writestr('manifest.json', json.dumps({'version': 1, 'files': files}, ensure_ascii=False))
            # Exclusive creation prevents another backup being overwritten.
            created = False
            try:
                with output.open('xb') as target, archive.open('rb') as src:
                    created = True
                    shutil.copyfileobj(src, target)
            except BaseException:
                if created:output.unlink(missing_ok=True)
                raise
    return len(files)


def restore(archive, destination, max_bytes=10 * 1024**3):
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('복원 대상은 아직 존재하지 않는 새 폴더여야 합니다.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / 'data'
        staged.mkdir()
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if len(names) != len(set(names)) or sum(i.file_size for i in infos) > max_bytes:
                raise ValueError('중복 파일 또는 복원 크기 제한 초과입니다.')
            if z.getinfo('manifest.json').file_size > 16 * 1024**2:
                raise ValueError('백업 목록이 너무 큽니다.')
            try:manifest = json.loads(z.read('manifest.json'),object_pairs_hook=manifest_object)
            except RecursionError:raise ValueError('백업 목록의 중첩이 너무 깊습니다.') from None
            if not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest['version'] != 1 or not isinstance(manifest.get('files'), dict):
                raise ValueError('지원하지 않는 백업입니다.')
            files = manifest['files']
            file_names={name.casefold() for name in files}
            if len(file_names) != len(files):
                raise ValueError('대소문자가 충돌하는 경로입니다.')
            if set(names) != {'manifest.json'} | {'data/' + name for name in files}:
                raise ValueError('백업 목록과 실제 파일이 다릅니다.')
            aliases={};checked_paths={}
            for name, expected in files.items():
                if not isinstance(expected, dict) or type(expected.get('size')) is not int or expected['size'] < 0 or not isinstance(expected.get('sha256'), str):
                    raise ValueError('잘못된 백업 파일 정보입니다.')
                if not re.fullmatch('[0-9a-f]{64}',expected['sha256']):
                    raise ValueError('백업 파일 해시 검증에 실패했습니다.')
                if z.getinfo('data/'+name).file_size!=expected['size']:
                    raise ValueError('백업 파일 크기가 목록과 다릅니다.')
                checked_paths[name]=checked_backup_path(name,aliases)
                parts=checked_paths[name]
                if any('/'.join(parts[:index]).casefold() in file_names for index in range(1,len(parts))):
                    raise ValueError('백업 파일과 디렉터리 경로가 충돌합니다.')
            for name, expected in files.items():
                parts=checked_paths[name]
                path = staged.joinpath(*parts)
                if not _inside(path.resolve(), staged.resolve()):
                    raise ValueError('복원 폴더 밖 경로입니다.')
                path.parent.mkdir(parents=True, exist_ok=True)
                digest=hashlib.sha256();size=0
                with z.open('data/'+name) as stream,path.open('wb') as output:
                    while block:=stream.read(CHUNK_SIZE):
                        size+=len(block)
                        if size>expected['size']:raise ValueError('백업 파일 크기가 목록과 다릅니다.')
                        output.write(block);digest.update(block)
                if size != expected['size'] or digest.hexdigest() != expected['sha256']:raise ValueError('백업 파일 검증에 실패했습니다.')
        db = staged / 'state.sqlite3'
        if not db.is_file():
            raise ValueError('백업에 데이터베이스가 없습니다.')
        with closing(sqlite3.connect(db)) as c, c:
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('데이터베이스가 손상되었습니다.')
            c.execute("INSERT OR REPLACE INTO settings VALUES('paused','true')")
            # Runtime ownership and installer side effects are outside data backup.
            # Preserve exact old records for review, but never adopt them after restore.
            vbox_records=dict(c.execute("SELECT key,value FROM settings WHERE key IN ('vbox_plan','vbox_preparation','vbox_installation')"))
            if vbox_records:
                # SQLite settings may contain legacy BLOBs. Preserve their exact
                # bytes without treating them as active installation receipts.
                snapshot={key:({'sqlite_type':'blob','hex':value.hex()} if isinstance(value,bytes) else value) for key,value in vbox_records.items()}
                c.execute('CREATE TABLE IF NOT EXISTS vbox_restore_archive(id INTEGER PRIMARY KEY,restored REAL NOT NULL,snapshot TEXT NOT NULL)')
                c.execute('INSERT INTO vbox_restore_archive(restored,snapshot) VALUES(?,?)',(time.time(),json.dumps(snapshot,ensure_ascii=False)))
                c.execute("DELETE FROM settings WHERE key IN ('vbox_plan','vbox_preparation','vbox_installation')")
            tables={row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            owner=c.execute("SELECT value FROM settings WHERE key='vm_owner'").fetchone()
            if owner:
                archive_setting(c,'vm_owner',owner[0])
                c.execute("DELETE FROM settings WHERE key='vm_owner'")
            if 'backup_requests' in tables and c.execute('SELECT 1 FROM backup_requests LIMIT 1').fetchone():
                # PIDs and request identity belong to the installation that made
                # the backup. Keep exact SQLite values, including legacy blobs.
                c.execute('CREATE TABLE IF NOT EXISTS backup_restore_archive(id INTEGER PRIMARY KEY,restored REAL NOT NULL,request_key,file,state,helper_pid,created)')
                c.execute('INSERT INTO backup_restore_archive(restored,request_key,file,state,helper_pid,created) SELECT ?,request_key,file,state,helper_pid,created FROM backup_requests',(time.time(),))
                c.execute('DELETE FROM backup_requests')
            for table in ('outbox','slack_inbox'):
                if table in tables:
                    c.execute(f"UPDATE {table} SET state='held_restored' WHERE state='pending'")
            if 'desktop_notifications' in tables:c.execute("UPDATE desktop_notifications SET state='suppressed' WHERE state IN ('pending','claimed')")
            if 'slack_conversations' in tables:c.execute("UPDATE slack_conversations SET state='held_restored' WHERE state IN ('received','queued','planning','reply_pending','control_ready')")
            profile=c.execute("SELECT value FROM settings WHERE key='execution_profile'").fetchone()
            if profile:
                archive_setting(c,'execution_profile',profile[0])
                c.execute("UPDATE settings SET value=? WHERE key='execution_profile'",(json.dumps(restored_execution_profile(profile[0])),))
            for key in ('slack', 'vm'):
                row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                if row:
                    archive_setting(c,key,row[0])
                    value=restored_connection(key,row[0])
                    c.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(value), key))
        # Windows rename refuses an existing destination; never replace user data.
        publish_restored(staged,destination)
    return len(files)


def inspect_backup(archive):
    """Fully validate in a temporary folder without replacing installed data."""
    archive = Path(archive)
    initial=archive.lstat()
    def valid(info):return stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not getattr(info,'st_file_attributes',0)&0x400
    if not valid(initial):raise ValueError('백업 파일은 링크가 아닌 독립된 일반 파일이어야 합니다.')
    with archive.open('rb') as source,tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        opened=os.fstat(source.fileno());current=archive.lstat()
        if not valid(opened) or not valid(current) or (initial.st_dev,initial.st_ino)!=(opened.st_dev,opened.st_ino) or (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):
            raise ValueError('백업 파일이 검사 시작 중 변경되었습니다.')
        destination = Path(temporary) / 'preview'
        count = restore(source, destination, max_bytes=512 * 1024**2)
        with closing(sqlite3.connect(destination / 'state.sqlite3')) as c:
            tasks = c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
            messages = c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
            encrypted = bool(c.execute("SELECT 1 FROM settings WHERE key='slack_secret_blob'").fetchone())
        return {'version': 1, 'files': count, 'tasks': tasks, 'messages': messages, 'encrypted_tokens': encrypted, 'restored': False}


def scheduled_backup(source, destination, parent_pid, timeout=300):
    try:lease=acquire(Path(destination).with_suffix('.job.lock'))
    except OSError:raise BusyBackup('같은 백업 요청이 이미 처리 중이거나 백업 폴더를 사용할 수 없습니다.') from None
    with lease:return _scheduled_backup(source,destination,parent_pid,timeout)


def _scheduled_backup(source, destination, parent_pid, timeout):
    from vm_runtime import pid_alive
    status = Path(destination).with_suffix('.status.json')
    deadline = time.monotonic() + timeout
    def record(state, message):
        if not write_status(status, {'state': state, 'message': message,'helper_pid':os.getpid()}):
            raise OSError('백업 상태 파일을 저장하지 못했습니다.')
    record('waiting', '앱과 감시 관리자 종료를 기다리고 있습니다.')
    try:
        while time.monotonic() < deadline:
            if not pid_alive(parent_pid):
                try:
                    count = backup(source, destination)
                    record('completed', f'{count}개 파일 백업 완료')
                    return count
                except BusyBackup:pass
            time.sleep(.25)
        raise ValueError('앱 종료 대기 시간이 초과되었습니다. 기존 데이터는 보존했습니다.')
    except Exception:
        record('failed', '백업하지 못했습니다. 앱 종료·폴더 권한·디스크 공간을 확인하세요.')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='앱을 종료한 상태에서 data 백업/새 폴더 복원')
    parser.add_argument('action', choices=['backup', 'restore', 'scheduled'])
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--wait-pid', type=int)
    args = parser.parse_args()
    try:
        if args.action == 'scheduled':
            if not args.wait_pid or args.wait_pid < 1:raise ValueError('종료 대기할 앱 PID가 필요합니다.')
            count = scheduled_backup(args.source, args.destination, args.wait_pid)
        else:count = (backup if args.action == 'backup' else restore)(args.source, args.destination)
        print(f'{args.action}: {count}개 파일 처리 완료')
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        parser.exit(1, f'처리하지 못했습니다: {exc}\n')
