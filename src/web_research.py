"""Free public-source research tools; no guest installation or arbitrary URL fetch."""
import hashlib,html,json,re,urllib.parse,urllib.request
MAX_BYTES=8*1024*1024
HOSTS={'commons.wikimedia.org','en.wikipedia.org','upload.wikimedia.org','thumb.wikimedia.org'}
UA='MOA-LocalResearch/1.0 (desktop public-source research)'
def checked_url(url):
 p=urllib.parse.urlsplit(url)
 if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443):raise ValueError('리서치 출처 주소가 허용 범위를 벗어났습니다.')
 return url
class Redirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):return super().redirect_request(req,fp,code,msg,headers,checked_url(newurl))
def fetch(url,limit=MAX_BYTES):
 req=urllib.request.Request(checked_url(url),headers={'User-Agent':UA})
 with urllib.request.build_opener(Redirect()).open(req,timeout=20) as response:
  checked_url(response.url);data=response.read(limit+1)
 if len(data)>limit:raise ValueError('리서치 응답 크기 한도를 초과했습니다.')
 return data
def plain(value):return html.unescape(re.sub('<[^>]*>','',str(value)))[:1600]
def query_url(host,params):return 'https://'+host+'/w/api.php?'+urllib.parse.urlencode({'action':'query','format':'json',**params})
def image_subject(query):
 if not isinstance(query,str) or not 1<=len(query.strip())<=200:raise ValueError('이미지 검색어는 1~200자입니다.')
 positive=re.sub(r'(?<!\S)-\S+',' ',query.lower())
 generic=set('photo photos photograph photographs photography picture pictures image images bitmap file jpeg jpg png painting drawing beautiful free high quality resolution search find download please the and with for'.split())
 terms={word.rstrip('s') for word in re.findall(r'[a-z]{3,}',positive) if word not in generic}
 if not terms:raise ValueError('검색 대상이 빠졌습니다. 원문 대상을 영어로 포함하세요. 예: flower photograph -painting -drawing. photograph만 검색하지 마세요.')
 return terms

def image_relevance(query,title,description):
 terms=image_subject(query);words={word.rstrip('s') for word in re.findall(r'[a-z]{3,}',plain(title+' '+description).lower())}
 return terms.issubset(words)

def image_search(query,files):
 image_subject(query)
 if not isinstance(query,str) or not 1<=len(query.strip())<=200:raise ValueError('이미지 검색어는 1~200자입니다.')
 data=json.loads(fetch(query_url('commons.wikimedia.org',{'generator':'search','gsrnamespace':6,'gsrsearch':query+' filetype:bitmap','gsrlimit':5,'prop':'imageinfo','iiprop':'url|mime|extmetadata','iiurlwidth':640}),1024*1024))
 errors=[]
 for page in sorted(data.get('query',{}).get('pages',{}).values(),key=lambda p:p.get('index',999)):
  info=(page.get('imageinfo') or [{}])[0];mime=info.get('mime');meta=info.get('extmetadata',{});license=plain(meta.get('LicenseShortName',{}).get('value',''))
  if mime not in ('image/jpeg','image/png') or not license:continue
  description=plain(meta.get('ImageDescription',{}).get('value',''))
  if not image_relevance(query,page.get('title',''),description):errors.append('검색 대상과 제목·설명 불일치');continue
  url=info.get('thumburl') or info.get('url','')
  try:
   body=fetch(url);is_png=body.startswith(b'\x89PNG\r\n\x1a\n');is_jpg=body.startswith(b'\xff\xd8\xff')
   if not (is_png or is_jpg):raise ValueError('검색 이미지 형식 확인 실패')
  except Exception as e:errors.append(type(e).__name__);continue
  name='research/image.'+('png' if is_png else 'jpg');path=files.path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
  result={'query':query,'title':page['title'],'source':info.get('descriptionurl') or 'https://commons.wikimedia.org/wiki/'+urllib.parse.quote(page['title'].replace(' ','_')),'image_url':url,'license':license,'license_url':plain(meta.get('LicenseUrl',{}).get('value','')),'author':plain(meta.get('Artist',{}).get('value','')),'description':plain(meta.get('ImageDescription',{}).get('value','')),'path':name,'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'visual_understanding':False,'runner':'moa-built-in-research'}
  result['subject_metadata_verified']=True
  files.path('research/sources.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
 raise ValueError('다운로드 가능한 출처·이용 조건이 있는 사진을 찾지 못했습니다. '+','.join(errors))
def web_search(query,files):
 if not isinstance(query,str) or not 1<=len(query.strip())<=200:raise ValueError('검색어는 1~200자입니다.')
 data=json.loads(fetch(query_url('en.wikipedia.org',{'list':'search','srsearch':query,'srlimit':5}),1024*1024))
 result={'query':query,'sources':[{'title':r['title'],'url':'https://en.wikipedia.org/?curid='+str(r['pageid']),'snippet':plain(r['snippet'])} for r in data.get('query',{}).get('search',[])],'scope':'Wikipedia public-source discovery; snippets are not full-page verification'}
 if not result['sources']:raise ValueError('공개 리서치 검색 결과가 없습니다.')
 path=files.path('research/results.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result

def verify(files):
 meta=files.path('research/sources.json');result=json.loads(meta.read_text(encoding='utf-8'));name=result.get('path')
 if name not in ('research/image.jpg','research/image.png'):raise ValueError('리서치 파일 경로 오류')
 if not image_relevance(result.get('query'),result.get('title',''),result.get('description','')):raise ValueError('이미지 출처 제목·설명이 검색 대상과 일치하지 않습니다.')
 body=files.path(name).read_bytes();remote=fetch(result['image_url']);sha=hashlib.sha256(body).hexdigest()
 if sha!=result.get('sha256') or hashlib.sha256(remote).hexdigest()!=sha:raise ValueError('독립 다운로드 검증에서 이미지 해시가 다릅니다.')
 return {'passed':True,'source':result['source'],'path':name,'sha256':sha,'bytes':len(body),'check':'subject metadata match, independent public-source re-fetch and local SHA-256 comparison','subject_metadata_verified':True,'visual_understanding':False}

# General public web research. Search results are discovery; actual pages are read separately.
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
import datetime
from research_http import fetch_public
class PageText(HTMLParser):
 def __init__(self):super().__init__();self.skip=0;self.parts=[];self.in_title=False;self.title=[]
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style','noscript','svg','nav','footer','header'):self.skip+=1
  if tag=='title':self.in_title=True
 def handle_endtag(self,tag):
  if tag in ('script','style','noscript','svg','nav','footer','header'):self.skip=max(0,self.skip-1)
  if tag=='title':self.in_title=False
  if tag in ('p','div','li','h1','h2','h3','tr','section','article'):self.parts.append('\n')
 def handle_data(self,data):
  if self.in_title:self.title.append(data)
  if not self.skip:self.parts.append(data)
 def result(self):return re.sub(r'[ \t]+',' ',re.sub(r'\n\s*\n+','\n',' '.join(self.parts))).strip()

def read_page(url):
 response=fetch_public(url);kind=response['content_type'].lower()
 if 'html' not in kind and not kind.startswith('text/'):raise ValueError('본문 읽기는 HTML·텍스트 페이지를 지원합니다. PDF·로그인·이미지 내용은 읽은 것으로 처리하지 않습니다.')
 encoding=re.search(r'charset=([\w-]+)',kind);encoding=encoding[1] if encoding else 'utf-8'
 try:raw=response['body'].decode(encoding,errors='replace')
 except LookupError:raw=response['body'].decode('utf-8',errors='replace')
 parser=PageText();parser.feed(raw);text=parser.result()
 if len(text)<100:raise ValueError('읽을 수 있는 본문이 너무 짧습니다. 로그인·차단 여부를 확인하세요.')
 return {'url':response['url'],'title':plain(' '.join(parser.title)) or response['url'],'text':text[:12000],'sha256':hashlib.sha256(text.encode()).hexdigest(),'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}

def save_web(files,result):
 path=files.path('research/results.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result

def web_search(query,files):
 if not isinstance(query,str) or not 1<=len(query.strip())<=300:raise ValueError('검색어는 1~300자입니다.')
 url='https://www.bing.com/search?'+urllib.parse.urlencode({'format':'rss','q':query})
 response=fetch_public(url);tree=ET.fromstring(response['body']);candidates=[]
 for item in tree.findall('./channel/item')[:8]:
  link=item.findtext('link','')
  if link.startswith('http://'):link='https://'+link[7:]
  candidates.append({'title':plain(item.findtext('title','')),'url':link,'snippet':plain(item.findtext('description',''))})
 if not candidates:raise ValueError('공개 웹 검색 결과가 없습니다. 검색어를 바꾸거나 출처 URL을 지정해 주세요.')
 sources=[];errors=[]
 for row in candidates:
  try:
   page=read_page(row['url']);sources.append({**row,**page})
  except Exception as e:errors.append({'url':row['url'],'error':str(e)[:180]})
  if len(sources)==3:break
 if not sources:raise ValueError('검색 결과는 있으나 본문을 읽지 못했습니다: '+json.dumps(errors[:3],ensure_ascii=False))
 prior=files.path('research/results.json')
 if prior.is_file():
  old=json.loads(prior.read_text(encoding='utf-8'));sources=[p for p in old.get('sources',[]) if p['url'] not in {s['url'] for s in sources}]+sources;sources=sources[-6:]
 return save_web(files,{'query':query,'sources':sources,'discovery':candidates,'unread_sources':errors,'scope':'public web search and fetched page text; currentness and conflicting claims require source review'})

def open_page(url,files):
 page=read_page(url);path=files.path('research/results.json');prior=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'query':url,'sources':[]}
 prior['sources']=[p for p in prior['sources'] if p['url']!=page['url']]+[page];prior['sources']=prior['sources'][-6:];prior['scope']='actual public page text';return save_web(files,prior)

_image_verify=verify

def verify(files):
 if files.path('research/results.json').is_file():
  result=json.loads(files.path('research/results.json').read_text(encoding='utf-8'));checks=[]
  for source in result.get('sources',[]):
   fresh=read_page(source['url']);original=re.sub(r'\s+',' ',source['text']).strip();current=re.sub(r'\s+',' ',fresh['text']).strip()
   anchors=[original[i:i+120] for i in range(0,min(len(original)-120,1200),240)]
   matches=sum(anchor in current for anchor in anchors)
   if not anchors or matches<max(1,len(anchors)//2):raise ValueError('독립 출처 확인에서 본문 근거가 달라졌습니다: '+source['url'])
   checks.append({'url':source['url'],'matched_passages':matches,'retrieved_at':fresh['retrieved_at']})
  if not checks:raise ValueError('확인할 본문 출처가 없습니다.')
  return {'passed':True,'check':'independent page re-fetch and evidence passage comparison','sources':checks,'visual_understanding':False}
 return _image_verify(files)

def write_report(files,content):
 path=files.path('research/results.json')
 if not path.is_file():return None
 result=json.loads(path.read_text(encoding='utf-8'));sources=result['sources']
 # Require real citations; never turn uncited generated prose into a verified report.
 if not isinstance(content,str) or len(content.strip())<100:raise ValueError('리서치 보고서 본문이 비어 있거나 너무 짧습니다. 원문 묶음을 완료 결과로 대신 제공하지 않습니다.')
 content=content.split('\n\n## 출처와 확인 범위\n\n',1)[0]
 content+='\n\n## 출처와 확인 범위\n\n'+'\n'.join('- '+s['title']+' — '+s['url']+' (열람: '+s.get('retrieved_at','')+')' for s in sources)+'\n\n웹에서 읽은 근거를 정리한 자료입니다. 검색 결과가 모든 출처를 포함하지는 않습니다.\n'
 md=files.path('research/report.md');md.write_text(content,encoding='utf-8')
 rendered='<!doctype html><html lang="ko"><meta charset="utf-8"><title>리서치 결과</title><style>body{font:16px/1.85 system-ui,sans-serif;max-width:900px;margin:60px auto;padding:25px;color:#312b27;background:#faf7f4}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>리서치 결과</h1><pre>'+html.escape(content)+'</pre></html>'
 rendered=rendered.replace('<pre>'+html.escape(content)+'</pre>',render_markdown(content))
 files.path('research/report.html').write_text(rendered,encoding='utf-8');return {'path':'research/report.md','summary':content[:450]}


def render_markdown(content):
 blocks=[];table=False
 for line in content.splitlines():
  escaped=html.escape(line)
  escaped=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',escaped)
  if line.startswith('|'):
   if re.fullmatch(r'[ |:\-]+',line):continue
   if not table:blocks.append('<table style="border-collapse:collapse;width:100%">');table=True
   blocks.append('<tr>'+''.join('<td style="border-bottom:1px solid #e5d9d0;padding:10px">'+html.escape(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>');continue
  if table:blocks.append('</table>');table=False
  heading=re.match(r'^(#{1,3}) (.*)',line)
  if heading:level=len(heading[1]);blocks.append('<h'+str(level)+'>'+html.escape(heading[2])+'</h'+str(level)+'>')
  elif line.startswith('- '):blocks.append('<p style="margin:6px 0">• '+escaped[2:]+'</p>')
  elif line.strip():blocks.append('<p>'+escaped+'</p>')
 if table:blocks.append('</table>')
 return ''.join(blocks)


def excerpt(text,query,limit=1000):
    words={word.lower() for word in re.findall(r'[A-Za-z]{4,}|[가-힣]{2,}',query)}
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    ranked=sorted(range(len(lines)),key=lambda i:(-sum(w in lines[i].lower() for w in words),i))
    selected=set();size=0
    for index in ranked:
        if len(lines[index])<40:continue
        block=[i for i in range(max(0,index-2),min(len(lines),index+3)) if i not in selected]
        cost=sum(len(lines[i])+1 for i in block)
        if size and size+cost>limit:continue
        selected.update(block);size+=cost
        if size>=limit:break
    return '\n'.join(lines[i] for i in sorted(selected))[:limit] or text[:limit]
