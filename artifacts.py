"""Bounded-memory delivery of allowlisted task artifacts."""
from contextlib import contextmanager,closing
import hashlib
import json
import sqlite3
import os
from pathlib import Path,PurePosixPath
import re
import stat

CHUNK_SIZE=64*1024
ROLES=('planner','developer','reviewer','reporter')


class ArtifactMissing(FileNotFoundError):pass


def allowed_names(plan):
    return {'report.md','report.json','artifacts.zip'}|{f'agents/{aid}/messages.json' for aid in (*ROLES,*(a['id'] for a in plan['agents']),'system','user')}


def location(root,tid,name,plan):
    if not isinstance(tid,str) or not re.fullmatch('[0-9a-f]{12}',tid):raise ArtifactMissing()
    if not isinstance(name,str) or '\\' in name or ':' in name or PurePosixPath(name).as_posix()!=name or any(part in ('.','..') for part in PurePosixPath(name).parts):raise ArtifactMissing()
    if name not in allowed_names(plan) and not re.fullmatch(r'vm/r\d+-(planner|developer|reviewer|reporter)-(before|after)\.png',name):raise ArtifactMissing()
    root=Path(root).resolve();target=root/'tasks'/tid/name
    current=root
    for part in target.relative_to(root).parts:
        current=current/part
        if current.is_symlink() or getattr(current,'is_junction',lambda:False)():raise ArtifactMissing()
    if target.resolve()!=target:raise ArtifactMissing()
    info=target.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ArtifactMissing()
    return target,info


def publication_entry(root,tid,name):
    if name.startswith('vm/'):return None
    database=Path(root).resolve()/'state.sqlite3'
    if not database.is_file():return None
    try:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
            columns={row[1] for row in c.execute('PRAGMA table_info(tasks)')}
            if 'artifact_publication' not in columns:return None
            row=c.execute('SELECT artifact_publication FROM tasks WHERE id=?',(tid,)).fetchone()
        if row is None:raise ArtifactMissing()
        if row[0] is None:return None # Legacy reports predate publication receipts.
        value=json.loads(row[0])
        if not isinstance(value,dict) or not isinstance(value.get('files'),dict):raise ArtifactMissing()
        entry=value['files'].get(name)
        if not isinstance(value,dict) or not re.fullmatch('[0-9a-f]{32}',str(value.get('report_id',''))) or not isinstance(entry,dict) or type(entry.get('bytes')) is not int or entry['bytes']<0 or not isinstance(entry.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',entry['sha256']):raise ArtifactMissing()
        return entry
    except (sqlite3.Error,ValueError,TypeError):raise ArtifactMissing() from None


@contextmanager
def open_artifact(root,tid,name,plan):
    path,_=location(root,tid,name,plan)
    expected=publication_entry(root,tid,name)
    with path.open('rb') as stream:
        current,now=location(root,tid,name,plan);opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=(now.st_dev,now.st_ino) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1:raise ArtifactMissing()
        if expected is not None:
            if opened.st_size!=expected['bytes'] or hashlib.file_digest(stream,'sha256').hexdigest()!=expected['sha256']:raise ArtifactMissing()
            stream.seek(0)
        yield stream,opened


def chunks(stream,length):
    remaining=length
    while remaining>0:
        block=stream.read(min(CHUNK_SIZE,remaining))
        if not block:break
        remaining-=len(block)
        yield block


def metadata(root,tid,plan):
    files=[]
    for name in sorted(allowed_names(plan)):
        try:
            with open_artifact(root,tid,name,plan) as (stream,info):files.append({'name':name,'bytes':info.st_size})
        except OSError:continue
    return {'task_id':tid,'files':files,'availability_only':True}


def content_type(name):
    return 'image/png' if name.endswith('.png') else 'application/zip' if name.endswith('.zip') else 'application/json; charset=utf-8' if name.endswith('.json') else 'text/plain; charset=utf-8'


def download_name(tid,name):
    if name=='artifacts.zip':return f'moa-{tid}-results.zip'
    if name.startswith('agents/'):return f'moa-{tid}-{name.split("/")[1]}-conversation.json'
    return f'moa-{tid}-{PurePosixPath(name).name}'
