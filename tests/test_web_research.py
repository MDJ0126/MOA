from contextlib import nullcontext
import json,hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import web_research as w
from app import LocalFiles
class ResearchTests(unittest.TestCase):
 def test_generic_photo_query_rejected_before_network(self):
  with patch.object(w,'fetch') as fetch,self.assertRaisesRegex(ValueError,'검색 대상'):
   w.image_search('photograph -painting -drawing',None)
  fetch.assert_not_called()

 def test_unrelated_first_result_skipped_for_matching_subject(self):
  def page(title,index):return {'title':title,'index':index,'imageinfo':[{'mime':'image/jpeg','url':'https://upload.wikimedia.org/test.jpg','extmetadata':{'LicenseShortName':{'value':'Public domain'}}}]}
  api={'query':{'pages':{'1':page('File:Boulevard du Temple.jpg',1),'2':page('File:Flower.jpg',2)}}}
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as d,patch.object(w,'fetch',side_effect=[json.dumps(api).encode(),b'\xff\xd8\xfffixture']):
   result=w.image_search('flower photograph -painting -drawing',LocalFiles(d))
   self.assertEqual(result['title'],'File:Flower.jpg');self.assertTrue(result['subject_metadata_verified'])

 def test_old_wrong_photo_cannot_pass_hash_only_verification(self):
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as d,patch.object(w,'fetch') as fetch:
   files=LocalFiles(d);files.path('research/sources.json').parent.mkdir()
   files.path('research/sources.json').write_text(json.dumps({'path':'research/image.jpg','query':'photograph -painting -drawing','title':'File:Boulevard du Temple.jpg'}))
   with self.assertRaisesRegex(ValueError,'검색 대상'):w.verify(files)
   fetch.assert_not_called()

 def test_search_and_independent_verification(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as d:
   f=LocalFiles(d);body=b'\xff\xd8\xfffixture';api={'query':{'pages':{'1':{'title':'File:Monkey.jpg','imageinfo':[{'mime':'image/jpeg','url':'https://upload.wikimedia.org/monkey.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:Monkey.jpg','extmetadata':{'LicenseShortName':{'value':'CC BY-SA 4.0'},'Artist':{'value':'Test'}}}]}}}}
   with patch.object(w,'fetch',side_effect=[json.dumps(api).encode(),body,body]):
    result=w.image_search('monkey',f);self.assertEqual(result['license'],'CC BY-SA 4.0');self.assertTrue(w.verify(f)['passed'])
   f.path('research/image.jpg').write_bytes(b'changed')
   with patch.object(w,'fetch',return_value=body),self.assertRaisesRegex(ValueError,'해시'):w.verify(f)
 def test_external_urls_blocked(self):
  for url in ['http://commons.wikimedia.org/a','https://localhost/a','https://commons.wikimedia.org.evil/a','https://user@commons.wikimedia.org/a','https://upload.wikimedia.org:444/a']:
   with self.subTest(url=url),self.assertRaises(ValueError):w.checked_url(url)
 def test_no_unlicensed_image(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as d,patch.object(w,'fetch',return_value=b'{}'),self.assertRaises(ValueError):w.image_search('monkey',LocalFiles(d))

 def test_photo_request_exposes_search_before_installation(self):
  from guest_agent import GuestAgent
  from unittest.mock import Mock
  vm=Mock();vm.activity.side_effect=lambda *a:nullcontext();vm.guest_history.return_value=[];vm.command.return_value={'exit_code':0,'output':'hash recorded'}
  def response(a):return {'choices':[{'message':{'content':json.dumps(a)},'finish_reason':'stop'}]}
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as d,patch('guest_agent.request_json',side_effect=[response({'tool':'search_images','command':'monkey','summary':'검색','passed':False}),response({'tool':'finish','command':'','summary':'출처 사진 확보','passed':True})]) as model,patch('web_research.image_search',return_value={'sha256':'a'*64,'source':'https://commons.wikimedia.org/wiki/File:Monkey.jpg'}):
   result=GuestAgent(vm,'test','http://localhost:8766').run({'id':'abcdef123456','title':'원숭이 사진 하나 구해와봐','agent':{'id':'builder'}},'developer',[],LocalFiles(d),lambda *a:None)
   self.assertEqual(json.loads(model.call_args_list[0].args[0].data)['response_format']['json_schema']['schema']['properties']['tool']['enum'],['command','search_images','search_web','read_web'])
   vm.command.assert_called_once();self.assertTrue(result['passed'])

 def test_verified_image_finishes_without_second_model_call_and_no_install(self):
  from guest_agent import GuestAgent
  from unittest.mock import Mock
  vm=Mock();vm.activity.side_effect=lambda *a:nullcontext();vm.guest_history.return_value=[];vm.command.return_value={'exit_code':0,'output':'VM receipt'};agent=GuestAgent(vm,'test','http://localhost:8766');agent.resources=Mock()
  body=b'\xff\xd8\xfffixture';source={'path':'research/image.jpg','source':'https://commons.wikimedia.org/wiki/File:Sunflower.jpg','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
  def search(query,files):
   files.path('research/image.jpg').parent.mkdir(exist_ok=True);files.path('research/image.jpg').write_bytes(body);files.path('research/sources.json').write_text(json.dumps(source));return source
  response={'choices':[{'message':{'content':json.dumps({'tool':'search_images','command':'sunflower','summary':'사진 검색','passed':False,'report':''})},'finish_reason':'stop'}]}
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as d,patch('guest_agent.request_json',return_value=response) as model,patch('web_research.image_search',side_effect=search),patch('web_research.verify',return_value={'passed':True,'sha256':source['sha256']}):
   files=LocalFiles(d);task={'id':'abcdef123456','title':'해바라기 사진을 구해 주세요','agent':{'id':'chief'}}
   result=agent.run(task,'developer',[],files,lambda *a:None);self.assertTrue(result['passed']);self.assertEqual(model.call_count,1)
   result=agent.run(task,'reviewer',[],files,lambda *a:None);self.assertTrue(result['passed']);self.assertEqual(model.call_count,1);self.assertEqual(vm.command.call_count,2)
   agent.resources.guest_prepare.assert_not_called()
 def test_failed_independent_source_check_never_fast_passes(self):
  from guest_agent import GuestAgent
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as d,patch('web_research.verify',return_value={'passed':False}):
   files=LocalFiles(d);files.path('research/sources.json').parent.mkdir();files.path('research/sources.json').write_text('{}');vm=Mock();vm.activity.side_effect=lambda *a:nullcontext();vm.guest_history.return_value=[]
   with self.assertRaisesRegex(ValueError,'통과하지 못'):GuestAgent(vm,'test','http://localhost:8766').run({'id':'abcdef123456','title':'사진','agent':{'id':'chief'}},'reviewer',[],files,lambda *a:None)
   vm.command.assert_not_called()

if __name__=='__main__':unittest.main()
