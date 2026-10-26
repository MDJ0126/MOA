import copy
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker
from setup_config import PRESETS,save_team,write_setting,manifest
from slack_service import SlackService
from slack_media import receive_images,upload_images
from guest_agent import GuestAgent
import slack_workflow

PNG=b'\x89PNG\r\n\x1a\n'+b'fixture image'


class Web:
    token='xoxb-fixture'
    def __init__(self):self.posts=[];self.uploads=[];self.fail=False;self.fail_image=False
    def chat_postMessage(self,**kwargs):
        if self.fail:raise ConnectionError('offline')
        self.posts.append(kwargs);return {'ts':'100.'+str(len(self.posts))}
    def files_upload_v2(self,**kwargs):
        self.uploads.append(kwargs)
        if self.fail_image:raise ConnectionError('lost upload response')
        return {'files':[{'id':'FUPLOAD'+str(len(self.uploads))}]}
    def files_info(self,**kwargs):return {'file':{'mimetype':'image/png','size':len(PNG),'url_private':'https://files.slack.com/files-pri/test.png'}}


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name));save_team(self.store,PRESETS[1])
        self.config={'enabled':True,'receive':True,'notify':True,'collaboration':True,'channel':'C12345678','allowed_users':['U12345678'],
                     'connections':[{'id':'shared','agent_id':'shared'}]}
        write_setting(self.store,'slack',self.config)
        from vm_runtime import DEFAULT
        write_setting(self.store,'vm',{**DEFAULT,'enabled':True});write_setting(self.store,'execution_profile',{'target_os':'linux','location':'shared_vm','budget_percent':50})
        self.service=SlackService(self.store);self.service.config=self.config
        self.web=Web();self.service.web={'shared':self.web};self.connection=self.config['connections'][0]

    def event(self,identity='EvNew',text='Create and check a file in the guest.'):
        return {'event_id':identity,'event':{'type':'app_mention','user':'U12345678','channel':'C12345678','text':text,'ts':'90.1'}}

    def ready(self):
        with self.store.connect() as c:c.execute('UPDATE tasks SET retry_at=0')

    def test_slack_confirmation_gates_each_guest_stage_and_carries_images(self):
        tid=self.service.receive(self.event(),self.connection);worker=Worker(self.store)
        seen=[]
        @contextmanager
        def stage(vm,task,role,actor,before=None):
            if before:before()
            folder=self.store.root/'tasks'/task['id']/'vm';folder.mkdir(parents=True,exist_ok=True)
            for suffix in ('before','after'):(folder/f'r0-{role}-{suffix}.png').write_bytes(PNG)
            yield
        def run(backend,task,role,history,files,log):
            seen.append((role,history))
            log('guest_plan',role+' public intent')
            result={'command':'echo evidence','exit_code':0,'output':'evidence','runner':'qemu-guest'}
            return {'summary':role+' public conclusion','passed':True,'guest_results':[result]}
        def export(vm,tid,role,files):
            import tarfile
            with tarfile.open(files.path('guest-workspace.tar.gz'),'w:gz'):pass
            return {'path':'guest-workspace.tar.gz'}
        from tests.test_leader_quality import response
        with patch('vm_runtime.VM.stage',stage),patch.object(GuestAgent,'run',run),patch('vm_runtime.VM.export_workspace',export),patch('leader_review.request_json',return_value=response()) as leader_model:
            self.assertFalse(worker.tick());self.assertEqual(seen,[])
            self.service.flush();self.ready();self.assertTrue(worker.tick())
            self.assertEqual([x[0] for x in seen],['planner'])
            self.ready();self.assertFalse(worker.tick());self.assertEqual(len(seen),1)
            self.web.fail=True;self.service.flush();self.ready();self.assertFalse(worker.tick())
            self.web.fail=False
            with self.store.connect() as c:c.execute('UPDATE outbox SET available_at=0')
            for _ in range(12):
                self.service.flush();self.ready();worker.tick()
                if self.store.snapshot()['tasks'][0]['status']=='completed':break
            self.service.flush()
        task=self.store.snapshot()['tasks'][0]
        self.assertEqual(task['status'],'completed');self.assertEqual(len(self.web.uploads),6)
        self.assertTrue(any('public intent' in post['text'] for post in self.web.posts))
        self.assertTrue(all(item['thread_ts']=='90.1' for item in self.web.posts+self.web.uploads))
        self.assertTrue(any('planner public conclusion' in h['content'] and h['slack_ts'] for h in seen[1][1]))
        self.assertTrue(json.loads(task['verification'])['passed'])
        self.assertEqual(leader_model.call_count,2)
        self.assertEqual(task['quality_review']['review_count'],2)

    def test_ui_idempotent_requests_are_mapped_to_a_slack_thread(self):
        tid=self.store.add('Guest UI task','llama',request_key='ui_request_123')
        with self.store.connect() as c:self.assertIsNotNone(c.execute('SELECT * FROM slack_threads WHERE task_id=?',(tid,)).fetchone())
        self.assertTrue(json.loads(self.store.snapshot()['tasks'][0]['execution_plan'])['environment']['guest_execution'])

    def test_ordinary_channel_thread_reply_routes_only_known_authorized_thread(self):
        tid=self.service.receive(self.event(),self.connection)
        event=self.event('EvReply','Please check the output');event['event'].update(type='message',channel_type='channel',thread_ts='90.1')
        self.assertEqual(self.service.receive(event,self.connection),tid)
        bot=copy.deepcopy(event);bot['event_id']='EvBot';bot['event']['bot_id']='BFOREIGN'
        self.assertIsNone(self.service.receive(bot,self.connection))
        foreign=copy.deepcopy(event);foreign['event_id']='EvForeign';foreign['event']['thread_ts']='500.1'
        self.assertIsNone(self.service.receive(foreign,self.connection))
        self.assertEqual(len(self.store.snapshot()['tasks']),1)

    def test_image_failure_never_reposts_text_or_reuploads_uncertain_file(self):
        tid=self.service.receive(self.event(),self.connection);self.service.flush()
        folder=self.store.root/'tasks'/tid/'vm';folder.mkdir(parents=True);(folder/'screen.png').write_bytes(PNG)
        self.store.deliver(tid,'builder','checker',{'summary':'Inspect this actual capture','images':['vm/screen.png']})
        self.web.fail_image=True;self.service.flush();posts=len(self.web.posts)
        with self.store.connect() as c:c.execute('UPDATE outbox SET available_at=0')
        restarted=SlackService(self.store);restarted.config=self.config;restarted.web={'shared':self.web};restarted.flush()
        self.assertEqual(len(self.web.posts),posts);self.assertEqual(len(self.web.uploads),1)
        self.assertTrue(slack_workflow.pending(self.store,tid))

    def test_inbound_image_is_bounded_authenticated_and_replay_safe(self):
        tid=self.store.add('image reference','llama')
        opener=type('Opener',(),{'open':lambda self,request,timeout:io.BytesIO(PNG)})()
        with patch('slack_media.urllib.request.build_opener',return_value=opener):
            one=receive_images(self.store,self.web,'EvImage',tid,[{'id':'F123456'}])
            two=receive_images(self.store,self.web,'EvImage',tid,[{'id':'F123456'}])
        self.assertEqual(one,two);self.assertEqual((self.store.root/'tasks'/tid/one[0]).read_bytes(),PNG)

    def test_inbound_image_rejects_arbitrary_urls_before_network(self):
        tid=self.store.add('image reference','llama')
        with patch.object(self.web,'files_info',return_value={'file':{'mimetype':'image/png','size':10,'url_private':'https://evil.invalid/image'}}),patch('slack_media.urllib.request.build_opener') as network:
            with self.assertRaises(ValueError):receive_images(self.store,self.web,'EvBad',tid,[{'id':'F123456'}])
            network.assert_not_called()

    def test_manifest_includes_thread_events_and_image_scopes(self):
        for name in ['모아 빌더','모아 체커','모아 팀','Local Agent Team','한글 이름']:
            self.assertRegex(manifest(name)['features']['bot_user']['display_name'],r'^[a-z0-9_.-]{1,80}$')
        data=manifest();self.assertIn('files:write',data['oauth_config']['scopes']['bot'])
        self.assertIn('files:read',data['oauth_config']['scopes']['bot'])
        self.assertIn('message.channels',data['settings']['event_subscriptions']['bot_events'])

    def test_model_cannot_finish_review_without_guest_execution(self):
        task={'id':'abcdef123456','title':'check','agent':{'id':'checker'}}
        vm=type('VM',(),{'guest_history':lambda self,tid:[]})()
        response={'choices':[{'message':{'content':json.dumps({'tool':'finish','summary':'looks good','passed':True})},'finish_reason':'stop'}]}
        with patch('guest_agent.request_json',return_value=response):
            with self.assertRaisesRegex(ValueError,'명령 실행 없이'):GuestAgent(vm,'test','http://localhost:8766').run(task,'reviewer',[],None,lambda *args:None)

    def test_model_receives_observed_guest_output_before_finishing(self):
        task={'id':'abcdef123456','title':'check','agent':{'id':'checker'}}
        vm=type('VM',(),{'guest_history':lambda self,tid:[], 'command':lambda *args:{'exit_code':0,'output':'actual guest output'}})()
        def response(action):return {'choices':[{'message':{'content':json.dumps(action)},'finish_reason':'stop'}]}
        with patch('guest_agent.request_json',side_effect=[response({'tool':'command','command':'cat file','summary':'read evidence'}),response({'tool':'finish','summary':'verified output','passed':True})]) as model:
            result=GuestAgent(vm,'test','http://localhost:8766').run(task,'reviewer',[],None,lambda *args:None)
        self.assertTrue(result['passed']);self.assertIn(b'actual guest output',model.call_args.args[0].data)
        first=json.loads(model.call_args_list[0].args[0].data)
        self.assertEqual(first['response_format']['json_schema']['schema']['properties']['tool']['enum'],['command','search_images','search_web','read_web'])

    def test_successful_guest_command_is_not_replayed_when_model_repeats_it(self):
        task={'id':'abcdef123456','title':'check','agent':{'id':'checker'}}
        from unittest.mock import Mock
        vm=Mock();vm.guest_history.return_value=[];vm.command.return_value={'command':'cat ready.txt','exit_code':0,'output':'READY'}
        def response(action):return {'choices':[{'message':{'content':json.dumps(action)},'finish_reason':'stop'}]}
        action={'tool':'command','command':'cat ready.txt','summary':'inspect','passed':True}
        with patch('guest_agent.request_json',side_effect=[response(action),response(action),response({'tool':'finish','command':'','summary':'READY confirmed','passed':True})]) as model:
            result=GuestAgent(vm,'test','http://localhost:8766').run(task,'reviewer',[],None,lambda *args:None)
        vm.command.assert_called_once();self.assertTrue(result['passed'])
        last=json.loads(model.call_args.args[0].data)
        self.assertEqual(last['response_format']['json_schema']['schema']['properties']['tool']['enum'],['finish'])
