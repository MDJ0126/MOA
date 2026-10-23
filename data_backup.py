"""Offline data backup and restore. Never starts or controls a VM."""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import time
import zipfile
from supervisor import acquire, write_status

SKIP = {'worker.lock', 'supervisor.lock', 'supervisor.stop', 'supervisor.json', 'model.key','state.sqlite3-wal','state.sqlite3-shm'}
CHUNK_SIZE=64*1024

class BusyBackup(ValueError):pass


def _inside(path, parent):
    return path == parent or parent in path.parents


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
            files = {}
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                for path in sorted(source.rglob('*')):
                    relative = path.relative_to(source)
                    if relative.parts[0].casefold() == 'vm' or relative.as_posix().casefold() in SKIP:
                        continue
                    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                        raise ValueError('링크가 포함된 데이터는 백업하지 않습니다.')
                    if not _inside(path.resolve(), source):
                        raise ValueError('데이터 폴더 밖 경로입니다.')
                    if path.is_file():
                        if path.stat(follow_symlinks=False).st_nlink!=1:raise ValueError('하드링크가 포함된 데이터는 백업하지 않습니다.')
                        name='state.sqlite3' if relative.as_posix().casefold()=='state.sqlite3' else relative.as_posix()
                        actual=snapshot if name=='state.sqlite3' else path
                        digest=hashlib.sha256();size=0
                        with actual.open('rb') as stream,z.open('data/'+name,'w',force_zip64=True) as stored:
                            while block:=stream.read(CHUNK_SIZE):stored.write(block);digest.update(block);size+=len(block)
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
            manifest = json.loads(z.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
                raise ValueError('지원하지 않는 백업입니다.')
            files = manifest['files']
            if len({name.casefold() for name in files}) != len(files):
                raise ValueError('대소문자가 충돌하는 경로입니다.')
            if set(names) != {'manifest.json'} | {'data/' + name for name in files}:
                raise ValueError('백업 목록과 실제 파일이 다릅니다.')
            aliases={}
            for name, expected in files.items():
                if not isinstance(expected, dict) or type(expected.get('size')) is not int or expected['size'] < 0 or not isinstance(expected.get('sha256'), str):
                    raise ValueError('잘못된 백업 파일 정보입니다.')
                parts = PurePosixPath(name).parts
                reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{prefix}{i}' for prefix in ('COM', 'LPT') for i in range(1, 10)}
                if not parts or PurePosixPath(name).as_posix() != name or any(p in ('.', '..') or p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts) or '\\' in name or ':' in name or name.startswith('/') or name.casefold() in SKIP or parts[0].casefold() == 'vm':
                    raise ValueError('잘못된 백업 경로입니다.')
                for i in range(1,len(parts)+1):
                    prefix='/'.join(parts[:i]);folded=prefix.casefold()
                    if folded in aliases and aliases[folded]!=prefix:raise ValueError('대소문자 디렉터리 경로가 충돌합니다.')
                    aliases[folded]=prefix
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
            for key in ('slack', 'vm'):
                row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                if row:
                    value = json.loads(row[0]); value['enabled'] = False
                    c.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(value), key))
        # Windows rename refuses an existing destination; never replace user data.
        if destination.exists():
            raise ValueError('복원 대상이 이미 생성되었습니다.')
        staged.rename(destination)
    return len(files)


def inspect_backup(archive):
    """Fully validate in a temporary folder without replacing installed data."""
    archive = Path(archive)
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        destination = Path(temporary) / 'preview'
        count = restore(archive, destination, max_bytes=512 * 1024**2)
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
