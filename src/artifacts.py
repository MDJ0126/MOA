"""Bounded-memory delivery of allowlisted task artifacts."""
from contextlib import contextmanager,closing
import hashlib
import json
import sqlite3
import os
from pathlib import Path,PurePosixPath
import re
import stat
from urllib.parse import quote

CHUNK_SIZE=64*1024
ROLES=('planner','developer','reviewer','reporter')


class ArtifactMissing(FileNotFoundError):pass


def allowed_names(plan):
    return {'report.md','report.json','artifacts.zip','results.zip','workspace/research/image.jpg','workspace/research/image.png','workspace/research/sources.json','workspace/research/results.json'}|{f'agents/{aid}/messages.json' for aid in (*ROLES,*(a['id'] for a in plan['agents']),'system','user')}


def location(root,tid,name,plan):
    if not isinstance(tid,str) or not re.fullmatch('[0-9a-f]{12}',tid):raise ArtifactMissing()
    if not isinstance(name,str) or '\\' in name or ':' in name or PurePosixPath(name).as_posix()!=name or any(part in ('.','..') for part in PurePosixPath(name).parts):raise ArtifactMissing()
    if name not in allowed_names(plan) and name not in attachment_names(root,tid) and not (name.startswith('deliverables/') and name in published_names(root,tid)) and not re.fullmatch(r'vm/r\d+-(planner|developer|reviewer|reporter)-(before|after|command-\d+)\.png',name):raise ArtifactMissing()
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
    if name in ('workspace/research/image.jpg','workspace/research/image.png'):
        folder=Path(root).resolve()/'tasks'/tid/'workspace/research'
        source=folder/'sources.json'
        try:
            info=source.lstat()
            if source.is_symlink() or info.st_nlink!=1 or info.st_size>65536:raise ArtifactMissing()
            value=json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(value,dict):raise ArtifactMissing()
            digest=value.get('sha256');size=value.get('bytes')
            if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) or type(size) is not int or not 0<size<=8*1024*1024:raise ArtifactMissing()
            return {'sha256':digest,'bytes':size}
        except (OSError,ValueError,TypeError):raise ArtifactMissing() from None
    if name.startswith(('vm/','attachments/')):return None
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
    for name in sorted(allowed_names(plan)|published_names(root,tid)|attachment_names(root,tid)):
        try:
            with open_artifact(root,tid,name,plan) as (stream,info):
                item={'name':name,'bytes':info.st_size,'kind':'deliverable' if name.startswith('deliverables/') else 'attachment' if name.startswith('attachments/') else 'internal','label':PurePosixPath(name).name}
                if name.startswith('deliverables/') and PurePosixPath(name).suffix.lower() in ('.md','.txt','.csv','.json','.py','.js','.css','.html'):
                    text=stream.read(8192).decode('utf-8',errors='replace')
                    if name.endswith('.html'):
                        from html.parser import HTMLParser
                        class TextOnly(HTMLParser):
                            def __init__(self):super().__init__();self.parts=[];self.ignore=0
                            def handle_starttag(self,tag,attrs):
                                if tag in ('script','style'):self.ignore+=1
                            def handle_endtag(self,tag):
                                if tag in ('script','style'):self.ignore=max(0,self.ignore-1)
                            def handle_data(self,data):
                                if not self.ignore:self.parts.append(data)
                        parser=TextOnly();parser.feed(text);text=' '.join(parser.parts)
                    item['preview']=text[:1600]
                files.append(item)
        except OSError:continue
    return {'task_id':tid,'files':files,'availability_only':True}


def content_type(name):
    name=name.lower()
    return 'application/pdf' if name.lower().endswith('.pdf') else 'image/webp' if name.lower().endswith('.webp') else 'image/gif' if name.lower().endswith('.gif') else 'image/jpeg' if name.lower().endswith('.jpeg') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if name.lower().endswith('.docx') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if name.lower().endswith('.xlsx') else 'application/vnd.openxmlformats-officedocument.presentationml.presentation' if name.lower().endswith('.pptx') else 'text/html; charset=utf-8' if name.endswith('.html') else 'text/csv; charset=utf-8' if name.endswith('.csv') else 'image/jpeg' if name.endswith('.jpg') else 'image/png' if name.endswith('.png') else 'application/zip' if name.endswith('.zip') else 'application/json; charset=utf-8' if name.endswith('.json') else 'text/plain; charset=utf-8'


def download_name(tid,name):
    if name=='artifacts.zip':return f'moa-{tid}-execution-records.zip'
    if name=='results.zip':return f'moa-{tid}-results.zip'
    if name.startswith('agents/'):return f'moa-{tid}-{name.split("/")[1]}-conversation.json'
    return f'moa-{tid}-{PurePosixPath(name).name}'


def published_names(root,tid):
    database=Path(root).resolve()/'state.sqlite3'
    if not database.is_file():return set()
    try:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
            row=c.execute('SELECT artifact_publication FROM tasks WHERE id=?',(tid,)).fetchone()
        value=json.loads(row[0]) if row and row[0] else {}
        return {name for name in value.get('files',{}) if isinstance(name,str) and name.startswith('deliverables/') and len(name)<240}
    except (sqlite3.Error,ValueError,TypeError,AttributeError):return set()


def disposition(tid,name):
    filename=download_name(tid,name)
    fallback=re.sub(r'[^A-Za-z0-9._-]','_',filename)
    kind='attachment' if name.endswith('.zip') or name.startswith('deliverables/') and not name.endswith(('.png','.jpg','.jpeg','.md','.txt')) else 'inline'
    return kind+'; filename="'+fallback+'"; filename*=UTF-8\'\''+quote(filename,safe='')


def attachment_names(root,tid):
    database=Path(root).resolve()/'state.sqlite3'
    if not database.is_file():return set()
    try:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
            rows=c.execute('SELECT path FROM slack_attachments WHERE task_id=?',(tid,)).fetchall()
        return {r[0] for r in rows if isinstance(r[0],str) and re.fullmatch(r'attachments/[a-f0-9]{64}\.(png|jpg)',r[0])}
    except sqlite3.Error:return set()
