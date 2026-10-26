"""Download Microsoft LTSC evaluation media only; never accept terms or install."""
import concurrent.futures,hashlib,json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
URL='https://software-static.download.prss.microsoft.com/db/download/444969d5-f34g-4e03-ac9d-1f9786c69161/19044.1288.211006-0501.21h2_release_svc_refresh_CLIENT_LTSC_EVAL_x64FRE_en-us.iso'
EXPECTED='e4ab2e3535be5748252a8d5d57539a6e59be8d6726345ee10e7afd2cb89fefb5'
SIZE=4898582528;CHUNK=32*1024*1024
def main():
 folder=ROOT/'runtime/vm-downloads';parts=folder/'windows-ltsc-eval.parts';parts.mkdir(exist_ok=True)
 old=folder/'windows-ltsc-2021-eval-x64.iso.part';target=folder/'windows-ltsc-2021-eval-x64.iso';started=time.time()
 def fetch(index):
  start=index*CHUNK;end=min(SIZE,start+CHUNK)-1;path=parts/str(index);length=end-start+1
  if path.is_file() and path.stat().st_size==length:return
  if old.is_file() and old.stat().st_size>end:
   with old.open('rb') as stream:stream.seek(start);data=stream.read(length)
   path.write_bytes(data);return
  for attempt in range(3):
   try:
    req=urllib.request.Request(URL,headers={'Range':f'bytes={start}-{end}'})
    with urllib.request.urlopen(req,timeout=90) as response:
     if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{SIZE}':raise ValueError('Range response mismatch')
     data=response.read(length+1)
    if len(data)!=length:raise ValueError('Download size mismatch')
    path.write_bytes(data);return
   except Exception:
    if attempt==2:raise
 count=(SIZE+CHUNK-1)//CHUNK
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
  for done,_ in enumerate(concurrent.futures.as_completed([pool.submit(fetch,i) for i in range(count)]),1):
   if done%16==0 or done==count:print(json.dumps({'downloaded_parts':done,'total_parts':count}),flush=True)
 temp=target.with_suffix('.verified-part');digest=hashlib.sha256()
 with temp.open('wb') as output:
  for index in range(count):
   data=(parts/str(index)).read_bytes();digest.update(data);output.write(data)
 if digest.hexdigest()!=EXPECTED:raise ValueError('Microsoft official SHA-256 mismatch; ISO not enabled')
 temp.replace(target)
 receipt={'path':target.relative_to(ROOT).as_posix(),'source':URL,'sha256':digest.hexdigest(),'official_hash_source':'https://download.microsoft.com/download/9/3/b/93bfb33f-643f-43c9-a36f-cf3611c72187/Windows10Enterprise21H2HashValues.pdf','edition':'Windows 10 Enterprise LTSC 2021 Evaluation','evaluation_days':90,'permanently_free':False,'terms_accepted':False,'os_installed':False,'download_seconds':time.time()-started}
 (ROOT/'reports/windows-environment-20261025/iso-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(receipt),flush=True)
if __name__=='__main__':main()
