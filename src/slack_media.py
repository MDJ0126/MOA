"""Private Slack image transport; never fetch arbitrary attachment URLs."""
import hashlib
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MAX_IMAGE = 8*1024*1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Slack 이미지 리디렉션을 허용하지 않습니다.')


def receive_images(store,client,event_id,tid,files):
    saved=[]
    if not isinstance(files,list) or len(files)>4:raise ValueError('한 메시지의 이미지는 최대 4개입니다.')
    for item in files:
        fid=item.get('id','') if isinstance(item,dict) else ''
        if not re.fullmatch(r'F[A-Z0-9]{5,}',fid):raise ValueError('Slack 파일 ID 오류')
        with store.connect() as c:prior=c.execute('SELECT path FROM slack_attachments WHERE event_id=? AND file_id=?',(event_id,fid)).fetchone()
        if prior:saved.append(prior[0]);continue
        info=client.files_info(file=fid)['file']
        mime=info.get('mimetype')
        if mime not in ('image/png','image/jpeg'):raise ValueError('Slack 첨부는 PNG·JPEG 이미지를 지원합니다.')
        if type(info.get('size')) is not int or not 0<info['size']<=MAX_IMAGE:raise ValueError('Slack 이미지는 8MB 이하여야 합니다.')
        url=info.get('url_private_download') or info.get('url_private','');target=urlparse(url)
        if target.scheme!='https' or target.hostname not in ('files.slack.com','files-origin.slack.com') or target.username or target.password or target.port not in (None,443):raise ValueError('Slack 공식 비공개 파일 주소만 허용합니다.')
        request=urllib.request.Request(url,headers={'Authorization':'Bearer '+client.token})
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as response:data=response.read(MAX_IMAGE+1)
        valid=data.startswith(b'\x89PNG\r\n\x1a\n') if mime=='image/png' else data.startswith(b'\xff\xd8\xff')
        if len(data)>MAX_IMAGE or not valid:raise ValueError('이미지 형식·크기 검증 실패')
        folder=store.root/'tasks'/tid/'attachments';folder.mkdir(parents=True,exist_ok=True)
        if folder.resolve()!=folder.absolute():raise ValueError('첨부 폴더 경로 오류')
        name=hashlib.sha256((event_id+fid).encode()).hexdigest()+('.png' if mime=='image/png' else '.jpg')
        path=folder/name
        if path.exists():
            info=path.lstat()
            if path.is_symlink() or info.st_nlink!=1 or getattr(info,'st_file_attributes',0)&0x400 or path.read_bytes()!=data:raise ValueError('기존 첨부 파일과 수신 이미지가 다릅니다.')
        else:
            with path.open('xb') as stream:stream.write(data)
        relative='attachments/'+name
        with store.connect() as c:c.execute('INSERT INTO slack_attachments VALUES(?,?,?,?,?)',(event_id,fid,tid,relative,mime))
        saved.append(relative)
    return saved


def upload_images(service,row,payload,thread):
    images=payload['content'].get('images',[]);results=payload['content'].get('files',[])
    if not isinstance(images,list) or not isinstance(results,list) or len(results)>8:raise ValueError('결과 첨부 목록 오류')
    images=images+results
    if not images:return
    if not isinstance(images,list) or len(images)>16:raise ValueError('단계별 이미지 한도 오류')
    store=service.store;tid=payload['task_id']
    if not re.fullmatch('[a-f0-9]{12}',tid):raise ValueError('VM 이미지 작업 ID 오류')
    folder=(store.root/'tasks'/tid).absolute()
    if folder.resolve()!=folder:raise ValueError('VM 작업 폴더 링크 오류')
    for name in images:
        if not isinstance(name,str):raise ValueError('이미지 경로 오류')
        path=folder/name
        user_result=name in results and name.startswith('deliverables/')
        if user_result:
            from artifacts import open_artifact
            import json
            with open_artifact(store.root,tid,name,json.loads(row['execution_plan'])) as (stream,info):pass
        research=name in ('workspace/research/image.jpg','workspace/research/image.png')
        if not (name.startswith('vm/') and path.suffix.lower()=='.png' or research or user_result) or path.resolve()!=path.absolute() or not path.resolve().is_relative_to(folder):raise ValueError('작업 VM 캡처만 전송할 수 있습니다.')
        info=path.stat()
        if info.st_nlink!=1 or not 0<info.st_size<=MAX_IMAGE:raise ValueError('VM 이미지 크기·링크 오류')
        with path.open('rb') as stream:
            magic=stream.read(8)
            if not (user_result or magic==b'\x89PNG\r\n\x1a\n' or research and path.suffix.lower()=='.jpg' and magic.startswith(b'\xff\xd8\xff')):raise ValueError('전송 이미지 형식 오류')
        receipt_path=(row['upload_scope']+'|' if row.get('upload_scope') else '')+name if isinstance(row,dict) else name
        with store.connect() as c:prior=c.execute('SELECT state FROM slack_uploads WHERE delivery_id=? AND path=?',(row['delivery_id'],receipt_path)).fetchone()
        suffix=hashlib.sha256(row.get('upload_scope','').encode()).hexdigest()[:8]+'-' if isinstance(row,dict) and row.get('upload_scope') else ''
        upload_name='moa-'+tid+'-'+str(row['delivery_id'])+'-'+suffix+path.name
        if prior:
            if prior[0]=='sent':continue
            # Reconcile an ambiguous response by looking up our unique filename;
            # never blindly start another upload after a crash or lost reply.
            identity=service.bot_user_ids.get(service._sending_connection)
            if not identity:raise ValueError('이미지 전송자의 Slack 인증 확인이 필요합니다.')
            match=None
            for page in range(1,6):
                listing=service._sending_client.files_list(channel=row['channel'],user=identity,count=100,page=page)
                match=next((f for f in listing.get('files',[]) if f.get('user')==identity and (f.get('name')==upload_name or f.get('title')==upload_name)),None)
                if match:
                    with store.connect() as c:c.execute("UPDATE slack_uploads SET state='sent',file_id=?,error='' WHERE delivery_id=? AND path=?",(match['id'],row['delivery_id'],receipt_path))
                    break
                if page>=listing.get('paging',{}).get('pages',1):break
            if match:continue
            raise ValueError('이전 이미지 업로드 결과가 미확인입니다. 중복 업로드를 막기 위해 수동 확인이 필요합니다.')
        with store.connect() as c:c.execute("INSERT INTO slack_uploads(delivery_id,path,state) VALUES(?,?,'uploading')",(row['delivery_id'],receipt_path))
        try:
            result=service._sending_client.files_upload_v2(channel=row['channel'],thread_ts=thread,
                       file=str(path),filename=upload_name,title=upload_name,initial_comment=('요청하신 결과 파일 · ' if user_result else '공개 출처 리서치 이미지 · ' if research else 'VM 실행 근거 · ')+payload['sender']+' · '+tid)
            fid=(result.get('files') or [result.get('file',{})])[0].get('id','')
            if not fid:raise ValueError('Slack 이미지 업로드 확인 ID가 없습니다.')
            with store.connect() as c:c.execute("UPDATE slack_uploads SET state='sent',file_id=? WHERE delivery_id=? AND path=?",(fid,row['delivery_id'],receipt_path))
        except Exception:
            with store.connect() as c:c.execute("UPDATE slack_uploads SET state='uncertain',error='결과 미확인: Slack 스레드 확인 필요' WHERE delivery_id=? AND path=?",(row['delivery_id'],receipt_path))
            raise
