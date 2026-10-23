"""Offline data backup and restore. Never starts or controls a VM."""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile
from supervisor import acquire

SKIP = {'worker.lock', 'supervisor.lock', 'supervisor.stop', 'supervisor.json', 'model.key'}


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
            raise ValueError('앱과 감시 관리자를 정상 종료한 뒤 백업하세요.') from exc
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            archive = Path(temporary) / 'backup.zip'
            files = {}
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                for path in sorted(source.rglob('*')):
                    relative = path.relative_to(source)
                    if relative.parts[0] == 'vm' or relative.as_posix() in SKIP:
                        continue
                    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                        raise ValueError('링크가 포함된 데이터는 백업하지 않습니다.')
                    if not _inside(path.resolve(), source):
                        raise ValueError('데이터 폴더 밖 경로입니다.')
                    if path.is_file():
                        data = path.read_bytes()
                        files[relative.as_posix()] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                        z.writestr('data/' + relative.as_posix(), data)
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
            if manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
                raise ValueError('지원하지 않는 백업입니다.')
            files = manifest['files']
            if len({name.casefold() for name in files}) != len(files):
                raise ValueError('대소문자가 충돌하는 경로입니다.')
            if set(names) != {'manifest.json'} | {'data/' + name for name in files}:
                raise ValueError('백업 목록과 실제 파일이 다릅니다.')
            for name, expected in files.items():
                parts = PurePosixPath(name).parts
                reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{prefix}{i}' for prefix in ('COM', 'LPT') for i in range(1, 10)}
                if not parts or PurePosixPath(name).as_posix() != name or any(p in ('.', '..') or p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts) or '\\' in name or ':' in name or name.startswith('/') or name in SKIP or parts[0] == 'vm':
                    raise ValueError('잘못된 백업 경로입니다.')
                path = staged.joinpath(*parts)
                if not _inside(path.resolve(), staged.resolve()):
                    raise ValueError('복원 폴더 밖 경로입니다.')
                content = z.read('data/' + name)
                if len(content) != expected['size'] or hashlib.sha256(content).hexdigest() != expected['sha256']:
                    raise ValueError('백업 파일 검증에 실패했습니다.')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='앱을 종료한 상태에서 data 백업/새 폴더 복원')
    parser.add_argument('action', choices=['backup', 'restore'])
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    try:
        count = (backup if args.action == 'backup' else restore)(args.source, args.destination)
        print(f'{args.action}: {count}개 파일 처리 완료')
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        parser.exit(1, f'처리하지 못했습니다: {exc}\n')
