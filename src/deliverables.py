"""User deliverables are separate from execution evidence and internal archives."""
import io,json,re,tarfile
from pathlib import PurePosixPath
MAX_FILE=8*1024*1024
MAX_TOTAL=24*1024*1024
INTERNAL={'guest-workspace.tar.gz','research.sha256','research/sources.json','research/results.json'}
def clean(name):
 p=PurePosixPath(name)
 return bool(name and not name.startswith('/') and '\\' not in name and ':' not in name and p.as_posix()==name and all(x not in ('.','..') and not x.startswith('.') for x in p.parts) and not any(x in ('__pycache__','node_modules','.git') for x in p.parts))
def collect(workspace):
 result={};total=0
 def add(name,body):
  nonlocal total
  if not clean(name) or name in INTERNAL or name.endswith(('.pyc','.log')):return
  if len(body)>MAX_FILE or total+len(body)>MAX_TOTAL or len(result)>=100:raise ValueError('사용자 결과 파일 크기·개수 한도를 초과했습니다.')
  if name in result and result[name]!=body:raise ValueError('결과 파일 이름이 충돌합니다: '+name)
  result[name]=body;total+=len(body)
 for path in sorted(workspace.rglob('*')):
  if path.is_file():
   if path.is_symlink() or path.resolve()!=path.absolute() or path.stat().st_nlink!=1:raise ValueError('결과 파일 링크 오류')
   name=path.relative_to(workspace).as_posix()
   if name not in INTERNAL:add(name.removeprefix('research/'),path.read_bytes())
 archive=workspace/'guest-workspace.tar.gz'
 if archive.is_file():
  with tarfile.open(archive,mode='r:gz') as bundle:
   for item in bundle:
    name=item.name[2:] if item.name.startswith('./') else item.name
    if item.isdir():continue
    if not item.isfile() or not clean(name) or item.size>MAX_FILE:raise ValueError('VM 사용자 결과 파일 형식·경로 오류')
    if name in INTERNAL:continue
    with bundle.extractfile(item) as stream:add(name,stream.read(MAX_FILE+1))
 meta=workspace/'research/sources.json'
 if meta.is_file():
  source=json.loads(meta.read_text(encoding='utf-8'));add('사진-출처.txt',('제목: '+source.get('title','')+'\n작가: '+source.get('author','')+'\n이용 조건: '+source.get('license','')+'\n출처: '+source.get('source','')+'\n이용 조건 URL: '+source.get('license_url','')+'\n').encode('utf-8'))
 return result
