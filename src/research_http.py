"""Bounded public HTTPS reads with DNS validation, pinned IPs and redirect checks."""
import http.client,ipaddress,socket,ssl,urllib.parse,zlib
MAX_BYTES=2*1024*1024

def target(url):
 p=urllib.parse.urlsplit(url)
 if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443) or any(c in url for c in '\r\n\x00'):raise ValueError('공개 HTTPS 주소만 읽을 수 있습니다.')
 host=p.hostname.encode('idna').decode('ascii')
 addresses=list(dict.fromkeys(a[4][0] for a in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))
 if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):raise ValueError('로컬·사설망 주소는 리서치 도구에서 접근할 수 없습니다.')
 return p,host,addresses

class PinnedHTTPS(http.client.HTTPSConnection):
 def __init__(self,host,address):super().__init__(host,443,timeout=15,context=ssl.create_default_context());self.address=address
 def connect(self):
  sock=socket.create_connection((self.address,443),self.timeout)
  try:self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
  except BaseException:sock.close();raise

def fetch_public(url,limit=MAX_BYTES):
 for _ in range(5):
  p,host,addresses=target(url);connection=PinnedHTTPS(host,addresses[0])
  try:
   connection.request('GET',urllib.parse.urlunsplit(('', '',p.path or '/',p.query,'')),headers={'Host':host,'User-Agent':'Mozilla/5.0 (compatible; MOA-Research/1.0)','Accept-Encoding':'identity'})
   response=connection.getresponse()
   if response.status in (301,302,303,307,308):url=urllib.parse.urljoin(url,response.getheader('Location',''));continue
   if response.status!=200:raise ValueError('출처 응답 HTTP '+str(response.status))
   encoding=response.getheader('Content-Encoding','identity')
   if encoding not in ('','identity','gzip'):raise ValueError('지원되지 않는 페이지 압축 방식입니다.')
   body=response.read(limit+1)
   if len(body)>limit:raise ValueError('페이지 읽기 크기 한도를 초과했습니다.')
   if encoding=='gzip':
    decoder=zlib.decompressobj(16+zlib.MAX_WBITS);body=decoder.decompress(body,limit+1)
    if len(body)>limit or decoder.unconsumed_tail:raise ValueError('압축 해제 크기 한도를 초과했습니다.')
   return {'url':url,'body':body,'content_type':response.getheader('Content-Type','')}
  finally:connection.close()
 raise ValueError('출처 리디렉션 한도를 초과했습니다.')
