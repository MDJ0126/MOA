from contextlib import nullcontext
import io,json,socket,tempfile,tarfile,unittest
from pathlib import Path
from unittest.mock import patch
import research_http as h,web_research as w,deliverables
from app import LocalFiles,Store,Worker
from artifacts import metadata,open_artifact,disposition
ROOT=Path(__file__).resolve().parent.parent
class GeneralResearchTests(unittest.TestCase):
 def test_private_dns_and_redirect_destinations_are_rejected(self):
  for ip in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1'):
   with self.subTest(ip=ip),patch('socket.getaddrinfo',return_value=[(2,1,6,'',(ip,443))]),self.assertRaises(ValueError):h.target('https://example.com/')
 def test_https_syntax_rejected_before_resolution(self):
  for url in ('http://example.com','https://user:secret@example.com','https://example.com:444/','https://example.com/\r\nX:bad'):
   with self.subTest(url=url),patch('socket.getaddrinfo') as dns,self.assertRaises(ValueError):h.target(url)
   dns.assert_not_called()
 def test_web_search_reads_actual_pages_and_keeps_urls(self):
  rss=b'<rss><channel><item><title>Space research</title><link>https://example.com/space</link><description>discovery only</description></item></channel></rss>'
  page={'url':'https://example.com/space','title':'Official space evidence','text':'actual verified page body '*40,'sha256':'a'*64,'retrieved_at':'now'}
  with tempfile.TemporaryDirectory(dir=ROOT) as d,patch.object(w,'fetch_public',return_value={'body':rss}),patch.object(w,'read_page',return_value=page):
   f=LocalFiles(d);result=w.web_search('space research',f);self.assertIn('actual verified',result['sources'][0]['text']);self.assertTrue(w.verify(f)['passed']);w.write_report(f,'# 조사 결과\n'+('실제 읽은 근거를 정리한 보고서입니다. '*10));self.assertIn(page['url'],f.path('research/report.md').read_text(encoding='utf-8'))
 def test_changed_evidence_does_not_pass(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   f=LocalFiles(d);w.save_web(f,{'sources':[{'url':'https://example.com','text':'original evidence '*80}]})
   with patch.object(w,'read_page',return_value={'text':'unrelated content '*80}),self.assertRaises(ValueError):w.verify(f)
 def test_report_html_escapes_source_content(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   f=LocalFiles(d);w.save_web(f,{'sources':[{'title':'Source','url':'https://example.com','text':'<script>alert(1)</script>'*20}]});w.write_report(f,'# 조사 결과\n'+('<script>alert(1)</script> '*10));body=f.path('research/report.html').read_text(encoding='utf-8');self.assertNotIn('<script>',body);self.assertIn('&lt;script&gt;',body)
 def test_user_bundle_extracts_real_vm_files_and_excludes_internal(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   root=Path(d);(root/'research').mkdir();(root/'research/image.jpg').write_bytes(b'photo');(root/'research/sources.json').write_text(json.dumps({'source':'https://example.com','license':'CC0'}))
   with tarfile.open(root/'guest-workspace.tar.gz','w:gz') as t:
    for name,body in [('answer.txt',b'actual user result'),('research.sha256',b'internal receipt')]:
     item=tarfile.TarInfo(name);item.size=len(body);t.addfile(item,io.BytesIO(body))
   result=deliverables.collect(root);self.assertEqual(result['answer.txt'],b'actual user result');self.assertIn('image.jpg',result);self.assertIn('사진-출처.txt',result);self.assertNotIn('guest-workspace.tar.gz',result);self.assertNotIn('research.sha256',result);self.assertNotIn('research/sources.json',result)
 def test_vm_path_traversal_is_rejected(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   with tarfile.open(Path(d)/'guest-workspace.tar.gz','w:gz') as t:
    item=tarfile.TarInfo('../outside.txt');item.size=1;t.addfile(item,io.BytesIO(b'x'))
   with self.assertRaises(ValueError):deliverables.collect(Path(d))
 def test_korean_download_header_is_ascii_and_rfc5987_encoded(self):
  header=disposition('abcdef123456','deliverables/사진-출처.txt');header.encode('ascii');self.assertIn("filename*=UTF-8''",header);self.assertIn('%EC',header)

 def test_research_cannot_request_install_before_observed_missing_command(self):
  from guest_agent import GuestAgent
  from unittest.mock import Mock
  vm=Mock();vm.activity.side_effect=lambda *a:nullcontext();vm.guest_history.return_value=[];manager=Mock();agent=GuestAgent(vm,'test','http://localhost:8766');agent.resources=manager
  response={'choices':[{'message':{'content':json.dumps({'tool':'resource','command':'program:python','summary':'install','passed':False})},'finish_reason':'stop'}]}
  with tempfile.TemporaryDirectory(dir=ROOT) as d,patch('guest_agent.request_json',return_value=response) as model,self.assertRaisesRegex(ValueError,'설치 전에 기본 도구'):
   agent.run({'id':'abcdef123456','title':'커피의 종류를 조사해 주세요','agent':{'id':'builder'}},'developer',[],LocalFiles(d),lambda *a:None)
  schema=json.loads(model.call_args.args[0].data)['response_format']['json_schema']['schema'];self.assertNotIn('resource',schema['properties']['tool']['enum']);manager.fulfill.assert_not_called();manager.available.assert_not_called()
 def test_report_output_is_useful_html_not_raw_markdown(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   f=LocalFiles(d);w.save_web(f,{'sources':[{'title':'Verified source','url':'https://example.com','text':'actual evidence '*80}]});w.write_report(f,'# 조사 결과\n\n## 비교\n\n| 항목 | 내용 |\n| --- | --- |\n| 근거 | 실제 확인 |\n\n- Source: https://example.com\n'+'actual supported conclusion '*4)
   body=f.path('research/report.html').read_text(encoding='utf-8');self.assertIn('<h2>비교</h2>',body);self.assertIn('<table',body);self.assertNotIn('<pre>',body)


 def test_empty_summary_is_not_published_as_raw_page_dump(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   f=LocalFiles(d);w.save_web(f,{'sources':[{'title':'Source','url':'https://example.com','text':'Evidence '*100}]})
   with self.assertRaisesRegex(ValueError,'본문이 비어'):w.write_report(f,'')
   self.assertFalse(f.path('research/report.md').exists())
 def test_dedicated_composer_produces_report_without_guest_install(self):
  from guest_agent import GuestAgent
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   f=LocalFiles(d);w.save_web(f,{'query':'research','sources':[{'title':'Source','url':'https://example.com','text':'actual evidence '*100}]})
   content='# 조사 결과\n'+('실제 확인한 자료에 근거한 요약입니다. '*12)
   response={'choices':[{'message':{'content':json.dumps({'content':content})},'finish_reason':'stop'}]}
   vm=Mock();vm.activity.side_effect=lambda *a:nullcontext()
   with patch('guest_agent.request_json',return_value=response):result=GuestAgent(vm,'test','http://localhost:8766').compose_report({'title':'일반 리서치'},f,lambda *a:None)
   self.assertEqual(result,content);vm.command.assert_not_called()
   w.write_report(f,result);self.assertIn('https://example.com',f.path('research/report.md').read_text(encoding='utf-8'))


 def test_excerpt_preserves_future_release_context(self):
  text='Introduction to software.\nUPCOMING RELEASE\nBlender 5.3 Beta\nNVIDIA rendering features and technology improvements for creative projects.\nExpected November 10\nMore documentation follows.'
  evidence=w.excerpt(text,'rendering features',1000);self.assertIn('UPCOMING RELEASE',evidence);self.assertIn('Expected November',evidence)
 def test_report_rewrite_does_not_duplicate_source_sections(self):
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   f=LocalFiles(d);w.save_web(f,{'sources':[{'title':'Source','url':'https://example.com','text':'evidence '*100}]});w.write_report(f,'# 조사\n'+('근거가 있는 한국어 보고서입니다. '*10));first=f.path('research/report.md').read_text(encoding='utf-8');w.write_report(f,first);self.assertEqual(first,f.path('research/report.md').read_text(encoding='utf-8'))


 def test_blocked_research_tries_another_builtin_without_install(self):
  from guest_agent import GuestAgent
  from unittest.mock import Mock
  vm=Mock();vm.activity.side_effect=lambda *a:nullcontext();vm.guest_history.return_value=[];vm.command.return_value={'exit_code':0,'output':'receipt'};agent=GuestAgent(vm,'test','http://localhost:8766');agent.resources=Mock()
  def response(a):return {'choices':[{'message':{'content':json.dumps(a)},'finish_reason':'stop'}]}
  actions=[{'tool':'search_web','command':'topic','summary':'검색','passed':False},{'tool':'search_images','command':'sunflower','summary':'대체 검색','passed':False},{'tool':'finish','command':'','summary':'사진 확보','passed':True}];events=[]
  with tempfile.TemporaryDirectory(dir=ROOT) as d,patch('guest_agent.request_json',side_effect=[response(a) for a in actions]),patch('web_research.web_search',side_effect=ValueError('출처 접근 차단')),patch('web_research.image_search',return_value={'sha256':'a'*64,'source':'https://commons.wikimedia.org/wiki/File:Flower.jpg'}):
   result=agent.run({'id':'abcdef123456','title':'일반 자료를 찾아 주세요','agent':{'id':'builder'}},'developer',[],LocalFiles(d),lambda *a:events.append(a))
  self.assertTrue(result['passed']);self.assertTrue(any(e[0]=='research_tool_error' for e in events));agent.resources.fulfill.assert_not_called()

 def test_report_review_still_requires_model_evidence_judgment(self):
  from guest_agent import GuestAgent
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory(dir=ROOT) as d:
   files=LocalFiles(d);w.save_web(files,{'sources':[{'title':'Source','url':'https://example.com','text':'actual evidence '*100}]});w.write_report(files,'# 조사 결과\n'+('실제 읽은 자료에서 확인했습니다. '*20))
   vm=Mock();vm.activity.side_effect=lambda *a:nullcontext();vm.guest_history.return_value=[];vm.command.return_value={'exit_code':0,'output':'RESEARCH_REVIEW_PASS'}
   response={'choices':[{'message':{'content':json.dumps({'tool':'finish','command':'','summary':'보고서의 주장에 근거가 부족합니다.','passed':False,'report':''})},'finish_reason':'stop'}]}
   with patch('web_research.verify',return_value={'passed':True}),patch('guest_agent.request_json',return_value=response) as model:
    result=GuestAgent(vm,'test','http://localhost:8766').run({'id':'abcdef123456','title':'자료 조사 보고서','agent':{'id':'chief'}},'reviewer',[],files,lambda *a:None)
   self.assertFalse(result['passed']);self.assertEqual(model.call_count,1);vm.command.assert_called_once()

if __name__=='__main__':unittest.main()
