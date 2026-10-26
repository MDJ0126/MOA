"""Visible Edge research in the interactive Windows guest, controlled over guest-local CDP."""
import base64,datetime,hashlib,json,time,urllib.parse
import windows_guest,web_research
from research_http import target

PS=r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new();
function Cdp($method,$params){
 $page=@(Invoke-RestMethod http://127.0.0.1:9222/json/list|Where-Object {$_.type -eq 'page'})[0];if(!$page){throw 'No interactive browser page'}
 $ws=[Net.WebSockets.ClientWebSocket]::new();$cts=[Threading.CancellationTokenSource]::new(25000)
 try{$ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl,$cts.Token).GetAwaiter().GetResult()
 $bytes=[Text.Encoding]::UTF8.GetBytes((@{id=1;method=$method;params=$params}|ConvertTo-Json -Depth 20 -Compress));$ws.SendAsync([ArraySegment[byte]]::new($bytes),[Net.WebSockets.WebSocketMessageType]::Text,$true,$cts.Token).GetAwaiter().GetResult()
 do{$stream=[IO.MemoryStream]::new();do{$buf=New-Object byte[] 65536;$r=$ws.ReceiveAsync([ArraySegment[byte]]::new($buf),$cts.Token).GetAwaiter().GetResult();$stream.Write($buf,0,$r.Count);if($stream.Length -gt 12000000){throw 'Browser response too large'}}while(!$r.EndOfMessage);$reply=[Text.Encoding]::UTF8.GetString($stream.ToArray())|ConvertFrom-Json;$stream.Dispose()}while($reply.id -ne 1)
 if($reply.error){throw ($reply.error|ConvertTo-Json -Compress)};return $reply.result
 }finally{$ws.Dispose();$cts.Dispose()}
}
'''

def run(vm,script,timeout=45):
    pid=windows_guest.rpc(vm,'guest-exec',{'path':r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','arg':['-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode((PS+script).encode('utf-16le')).decode()],'capture-output':True})['pid']
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=windows_guest.rpc(vm,'guest-exec-status',{'pid':pid})
        if result.get('exited'):
            stdout=base64.b64decode(result.get('out-data','')).decode('utf-8-sig',errors='replace').strip()
            if result.get('exitcode') or result.get('out-truncated'):raise ValueError('실제 브라우저 제어 실패: '+base64.b64decode(result.get('err-data','')).decode('utf-8',errors='replace')[:600])
            return json.loads(stdout) if stdout else None
        time.sleep(.15)
    raise TimeoutError('VM 실제 브라우저 응답 시간이 초과되었습니다.')

def ensure(vm):
    return run(vm,r'''try{$pages=@(Invoke-RestMethod http://127.0.0.1:9222/json/list|Where-Object {$_.type -eq 'page'})}catch{$pages=@()}
if(!$pages.Count){$user=(Get-CimInstance Win32_ComputerSystem).UserName;if(!$user){throw 'Windows 로그인 세션이 없습니다'}
$edge='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe';if(!(Test-Path $edge)){throw 'Microsoft Edge가 없습니다'}
$action=New-ScheduledTaskAction -Execute $edge -Argument '--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=C:\MOA\BrowserProfile --no-first-run --no-default-browser-check --start-maximized about:blank'
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'MOA-VisibleBrowser' -Action $action -Principal $principal -Settings $settings -Force|Out-Null;Start-ScheduledTask -TaskName 'MOA-VisibleBrowser'
for($i=0;$i -lt 40;$i++){Start-Sleep -Milliseconds 300;try{$pages=@(Invoke-RestMethod http://127.0.0.1:9222/json/list|Where-Object {$_.type -eq 'page'});if($pages.Count){break}}catch{}};if(!$pages.Count){throw '실제 화면의 Edge를 시작하지 못했습니다'}}
Cdp 'Page.bringToFront' @{}|Out-Null;@{ready=$true}|ConvertTo-Json -Compress''')

def evaluate(vm,expression):
    encoded=base64.b64encode(expression.encode()).decode()
    return run(vm,"$expression=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"'));$r=Cdp 'Runtime.evaluate' @{expression=$expression;returnByValue=$true;awaitPromise=$true};if($r.exceptionDetails){throw ($r.exceptionDetails|ConvertTo-Json -Depth 8)};ConvertTo-Json -InputObject $r.result.value -Depth 16 -Compress")

def navigate(vm,url):
    target(url)
    encoded=base64.b64encode(url.encode()).decode()
    run(vm,"$url=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"'));$r=Cdp 'Page.navigate' @{url=$url};if($r.errorText){throw $r.errorText};Cdp 'Page.bringToFront' @{}|Out-Null;@{navigated=$true}|ConvertTo-Json -Compress")
    for _ in range(30):
        ready=evaluate(vm,"({ready:document.readyState,url:location.href,length:document.body?.innerText.length||0})")
        if ready and ready['url']!='about:blank' and ready['ready']=='complete':return
        time.sleep(.3)
    raise TimeoutError('실제 브라우저 페이지 로딩이 완료되지 않았습니다.')

def snapshot(vm,files,stage,log):
    name='research/browser-'+stage+'-'+str(time.time_ns())+'.png';path=files.path(name);path.parent.mkdir(parents=True,exist_ok=True);vm.capture(path.resolve())
    log('browser_action',json.dumps({'stage':stage,'screenshot':name,'runner':'windows-visible-browser'},ensure_ascii=False));return name

def page(vm,url):
    navigate(vm,url)
    value=evaluate(vm,"({url:location.href,title:document.title,text:document.body.innerText.slice(0,12000)})")
    target(value['url'])
    if len(value.get('text',''))<100:raise ValueError('실제 브라우저에서 읽을 수 있는 본문이 부족합니다.')
    value.update(sha256=hashlib.sha256(value['text'].encode()).hexdigest(),retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    return value

def image_bytes(vm,url):
    web_research.checked_url(url)
    # Fetch executes in the visible browser's renderer, never on the MOA host.
    data=evaluate(vm,"(async()=>{const r=await fetch("+json.dumps(url)+");if(!r.ok)throw Error('Image HTTP '+r.status);const b=await r.blob();if(b.size>8388608)throw Error('Image exceeds 8 MiB');return await new Promise((ok,fail)=>{const f=new FileReader();f.onload=()=>ok(f.result.split(',')[1]);f.onerror=fail;f.readAsDataURL(b)})})()")
    body=base64.b64decode(data,validate=True)
    if len(body)>web_research.MAX_BYTES or not(body.startswith(b'\xff\xd8\xff') or body.startswith(b'\x89PNG\r\n\x1a\n')):raise ValueError('브라우저 이미지 형식·용량 확인 실패')
    return body

def save_guest_image(vm,tid,name,body):
    import re
    if not re.fullmatch('[a-f0-9]{12}',tid):raise ValueError('VM 이미지 작업 ID 오류')
    folder='C:/MOA/tasks/'+tid+'/research'
    run(vm,"New-Item -ItemType Directory -Force -Path '"+folder+"'|Out-Null;@{created=$true}|ConvertTo-Json -Compress")
    handle=windows_guest.rpc(vm,'guest-file-open',{'path':folder+'\\'+name.split('/')[-1],'mode':'wb'})
    try:
        for offset in range(0,len(body),49152):
            chunk=body[offset:offset+49152];result=windows_guest.rpc(vm,'guest-file-write',{'handle':handle,'buf-b64':base64.b64encode(chunk).decode()})
            if result.get('count')!=len(chunk):raise ValueError('VM 이미지 파일 저장 길이 불일치')
        windows_guest.rpc(vm,'guest-file-flush',{'handle':handle})
    finally:windows_guest.rpc(vm,'guest-file-close',{'handle':handle})

def image_search(vm,query,files,log):
    web_research.image_subject(query)
    url='https://commons.wikimedia.org/w/index.php?'+urllib.parse.urlencode({'title':'Special:Search','search':query+' filetype:bitmap','ns6':1})
    navigate(vm,url);search_shot=snapshot(vm,files,'search',log)
    candidates=evaluate(vm,"Array.from(document.querySelectorAll('.mw-search-result-heading a')).map(a=>({title:a.textContent,url:a.href})).filter(a=>/\\.(jpg|jpeg|png)$/i.test(a.title)).slice(0,8)") or []
    terms=web_research.image_subject(query)
    candidates.sort(key=lambda c:(-sum(c['title'].lower().removeprefix('file:').startswith(t) for t in terms),len(c['title'])))
    for candidate in candidates:
        if not web_research.image_relevance(query,candidate['title'],''):continue
        log('progress','VM 브라우저에서 이미지 출처를 열고 있습니다: '+candidate['title'])
        navigate(vm,candidate['url'])
        meta=evaluate(vm,"({title:document.querySelector('#firstHeading')?.innerText||document.title,source:location.href,image_url:document.querySelector('.fullImageLink a')?.href,description:document.querySelector('.description')?.innerText||'',license:Array.from(document.querySelectorAll('.licensetpl_short')).map(e=>e.innerText).join('; '),license_url:document.querySelector('.licensetpl_link a, a[rel=license]')?.href||'',author:document.querySelector('#fileinfotpl_aut + td')?.innerText||''})")
        if not meta.get('image_url') or not meta.get('license'):continue
        source_shot=snapshot(vm,files,'source',log)
        # A bounded preview is the same image rendered on the file page; avoids enormous originals.
        preview=evaluate(vm,"document.querySelector('.fullImageLink img')?.src||null")
        image_url=preview or meta['image_url'];body=image_bytes(vm,image_url)
        navigate(vm,image_url);image_shot=snapshot(vm,files,'image',log)
        name='research/image.'+('png' if body.startswith(b'\x89PNG') else 'jpg');files.path(name).write_bytes(body)
        tid=getattr(files,'task_id',None) or files.path('research').parent.parent.name
        if __import__('re').fullmatch('[a-f0-9]{12}',tid):save_guest_image(vm,tid,name,body)
        meta.update(query=query,image_url=image_url,path=name,sha256=hashlib.sha256(body).hexdigest(),bytes=len(body),visual_understanding=False,subject_metadata_verified=True,runner='windows-visible-browser',browser_evidence=[search_shot,source_shot,image_shot])
        files.path('research/sources.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        return meta
    raise ValueError('실제 브라우저 검색에서 대상·이용 조건이 확인되는 이미지를 찾지 못했습니다. 호스트 검색으로 대체하지 않았습니다.')

def execute(vm,tool,command,files,log):
    ensure(vm)
    if tool=='search_images':return image_search(vm,command,files,log)
    if tool=='read_web':
        source=page(vm,command);snapshot(vm,files,'read',log)
        existing=files.path('research/results.json');result=json.loads(existing.read_text(encoding='utf-8')) if existing.is_file() else {'query':command,'sources':[]}
        result['sources']=[s for s in result['sources'] if s['url']!=source['url']]+[source]
        result['runner']='windows-visible-browser';return web_research.save_web(files,result)
    if tool=='search_web':
        if not isinstance(command,str) or not 1<=len(command.strip())<=300:raise ValueError('검색어는 1~300자입니다.')
        navigate(vm,'https://www.bing.com/search?'+urllib.parse.urlencode({'q':command}));snapshot(vm,files,'search',log)
        candidates=evaluate(vm,"Array.from(document.querySelectorAll('li.b_algo h2 a')).map(a=>({title:a.innerText,url:a.href})).slice(0,5)") or []
        sources=[]
        for item in candidates:
            try:sources.append(page(vm,item['url']))
            except (ValueError,TimeoutError):continue
            if len(sources)>=2:break
        if not sources:raise ValueError('VM 브라우저에서 검색 출처 본문을 읽지 못했습니다.')
        snapshot(vm,files,'read',log)
        return web_research.save_web(files,{'query':command,'sources':sources,'runner':'windows-visible-browser','scope':'Actual interactive Windows Edge search and page DOM reading; source text is untrusted evidence.'})
    if tool=='verify_research':
        image=files.path('research/sources.json')
        if image.is_file():
            meta=json.loads(image.read_text(encoding='utf-8'));navigate(vm,meta['source'])
            if not web_research.image_relevance(meta['query'],meta['title'],meta.get('description','')):raise ValueError('검색 대상과 출처 메타데이터가 다릅니다.')
            body=image_bytes(vm,meta['image_url']);digest=hashlib.sha256(body).hexdigest()
            if digest!=meta['sha256'] or digest!=hashlib.sha256(files.path(meta['path']).read_bytes()).hexdigest():raise ValueError('브라우저 재조회 이미지 무결성 불일치')
            navigate(vm,meta['image_url']);snapshot(vm,files,'review',log)
            return {'passed':True,'source':meta['source'],'path':meta['path'],'sha256':digest,'visual_understanding':False,'subject_metadata_verified':True,'check':'Visible browser source revisit, metadata match and independent browser image re-fetch; no semantic visual classification','runner':'windows-visible-browser'}
        result=json.loads(files.path('research/results.json').read_text(encoding='utf-8'));checks=[]
        for source in result['sources'][:4]:
            current=page(vm,source['url']);checks.append({'url':current['url'],'read':True})
            old=source.get('text','');anchors=[s.strip() for s in old.splitlines() if len(s.strip())>60][:3]
            if not anchors or not any(a in current['text'] for a in anchors):raise ValueError('브라우저 재열람에서 저장한 본문 근거를 확인하지 못했습니다.')
        if not checks:raise ValueError('검증할 출처가 없습니다.')
        snapshot(vm,files,'review',log);return {'passed':True,'sources':checks,'runner':'windows-visible-browser','check':'Independent visible browser page revisit and stored passage comparison'}
    raise ValueError('지원하지 않는 브라우저 도구입니다.')
