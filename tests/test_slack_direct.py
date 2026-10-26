import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import Store,Worker
from setup_config import PRESETS,save_team,write_setting,prepare_slack
from slack_service import SlackService,test_connection
import slack_direct
import slack_workflow

PNG=b'\x89PNG\r\n\x1a\nfixture'


class Peer:
    def __init__(self,user):self.user=user;self.posts=[];self.uploads=[];self.opens=[];self.downloads=[];self.token='xoxb-fixture';self.lose_reply=False;self.messages=[]
    def conversations_open(self,**kw):self.opens.append(kw);return {'channel':{'id':'DPEERS123'}}
    def chat_postMessage(self,**kw):
        self.posts.append(kw);result={'ts':str(100+len(self.posts))+'.1'}
        self.messages.append({**kw,**result,'user':self.user})
        if self.lose_reply:raise ConnectionError('lost response')
        return result
    def conversations_history(self,**kw):return {'messages':self.messages}
    def files_upload_v2(self,**kw):self.uploads.append(kw);return {'files':[{'id':'FIMAGE123'}]}
    def files_info(self,**kw):
        self.downloads.append(kw)
        return {'file':{'size':len(PNG),'mimetype':'image/png','url_private':'https://files.slack.com/files-pri/example'}}


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);save_team(self.store,PRESETS[1])
        self.config={'enabled':True,'receive':True,'notify':True,'collaboration':True,'team_dm':True,
                     'channel':'C12345678','allowed_users':['U12345678'],
                     'connections':[{'id':'jiwoo','agent_id':'builder'},{'id':'seoyeon','agent_id':'checker'}]}
        write_setting(self.store,'slack',self.config)
        from vm_runtime import DEFAULT
        write_setting(self.store,'vm',{**DEFAULT,'enabled':True})
        self.service=SlackService(self.store);self.service.config=self.config
        self.first=Peer('UJIWOO123');self.second=Peer('USEOYEON123')
        self.service.web={'jiwoo':self.first,'seoyeon':self.second}
        self.service.bot_user_ids={'jiwoo':self.first.user,'seoyeon':self.second.user}
        self.tid=self.store.add('Create evidence in real VM','llama')
        self.service.flush()

    def handoff(self,sender='builder',recipient='checker',image=False):
        content={'summary':'파일을 만들었습니다. 검증해 주세요.'}
        if image:
            folder=Path(self.temp.name)/'tasks'/self.tid/'vm';folder.mkdir(parents=True,exist_ok=True);(folder/'screen.png').write_bytes(PNG)
            content['images']=['vm/screen.png']
        self.store.deliver(self.tid,sender,recipient,content)
        self.service.flush()
        with self.store.connect() as c:return dict(c.execute('SELECT * FROM slack_direct ORDER BY delivery_id DESC LIMIT 1').fetchone())

    def echo(self,row,event_id='EvPeer',connection=None,**changes):
        event={'type':'message','channel_type':'im','channel':row['channel'],'ts':row['ts'],'user':row['sender_user'],'bot_id':'BMANAGED','subtype':'bot_message'}
        event.update(changes)
        target=connection or next(c for c in self.config['connections'] if c['id']==row['recipient_connection'])
        return self.service.receive({'event_id':event_id,'event':event},target)

    def test_next_vm_stage_waits_for_recipient_and_both_directions_work(self):
        row=self.handoff();self.assertTrue(slack_workflow.pending(self.store,self.tid))
        self.assertEqual(self.first.opens[-1]['users'],self.second.user)
        self.assertFalse(Worker(self.store).tick())
        self.echo(row);self.service.flush()
        self.assertFalse(slack_workflow.pending(self.store,self.tid))
        history=slack_workflow.history(self.store,self.tid)
        self.assertEqual(json.loads(history[-1]['content'])['transport'],'slack_dm')
        reverse=self.handoff('checker','builder');self.assertEqual(self.second.opens[-1]['users'],self.first.user)
        self.echo(reverse,'EvReverse');self.service.flush()
        self.assertFalse(slack_workflow.pending(self.store,self.tid))
        self.assertEqual(len(self.store.snapshot()['tasks']),1)

    def test_recipient_downloads_image_with_own_credentials_and_no_duplicates(self):
        row=self.handoff(image=True);self.assertEqual(len(self.first.uploads),1)
        self.echo(row)
        opener=type('Opener',(),{'open':lambda self,request,timeout:io.BytesIO(PNG)})()
        with patch('slack_media.urllib.request.build_opener',return_value=opener):self.service.flush();self.service.flush()
        self.assertEqual(len(self.second.downloads),1)
        self.assertEqual(len(self.first.uploads),2,'one DM copy and one reporting-thread copy')
        self.assertFalse(slack_workflow.pending(self.store,self.tid))
        with self.store.connect() as c:paths=json.loads(c.execute('SELECT received_images FROM slack_direct').fetchone()[0])
        self.assertEqual((self.store.root/'tasks'/self.tid/paths[0]).read_bytes(),PNG)

    def test_foreign_sender_wrong_channel_wrong_recipient_cannot_acknowledge(self):
        row=self.handoff()
        self.echo(row,'EvForeign',user='UFOREIGN123')
        self.echo(row,'EvWrongChannel',channel='DOTHER123')
        self.echo(row,'EvWrongRecipient',connection=self.config['connections'][0])
        self.service.flush()
        self.assertTrue(slack_workflow.pending(self.store,self.tid))
        self.assertEqual(len(self.store.snapshot()['tasks']),1)

    def test_bot_message_without_user_is_matched_to_authenticated_bot_id(self):
        self.service.bot_ids={'jiwoo':'BJIWOO123','seoyeon':'BSEOYEON123'}
        row=self.handoff()
        self.echo(row,'EvBotOnly',user=None,bot_id='BJIWOO123')
        self.service.flush()
        self.assertFalse(slack_workflow.pending(self.store,self.tid))

    def test_shared_channel_handoff_waits_for_real_recipient_event_and_image_access(self):
        self.config.update(team_dm=False,channel_receipts=True)
        write_setting(self.store,'slack',self.config)
        self.tid=self.store.add('Channel collaboration','llama');self.service.flush()
        row=self.handoff(image=True)
        self.assertTrue(row['channel'].startswith('C'))
        self.assertTrue(slack_workflow.pending(self.store,self.tid))
        self.assertEqual(self.first.opens,[],'channel mode never opens bot-to-bot DMs')
        self.echo(row,'EvChannelPeer',channel_type='channel')
        opener=type('Opener',(),{'open':lambda self,request,timeout:io.BytesIO(PNG)})()
        with patch('slack_media.urllib.request.build_opener',return_value=opener):self.service.flush()
        self.assertFalse(slack_workflow.pending(self.store,self.tid))
        self.assertEqual(json.loads(slack_workflow.history(self.store,self.tid)[-1]['content'])['transport'],'slack_channel')

    def test_dm_upload_failure_does_not_advance_or_repost(self):
        row=self.handoff(image=True);self.echo(row)
        with patch.object(self.second,'files_info',side_effect=ConnectionError('recipient cannot access')):self.service.flush()
        posts=len(self.first.posts)
        with self.store.connect() as c:c.execute('UPDATE outbox SET available_at=0')
        with patch.object(self.second,'files_info',side_effect=ConnectionError('still denied')):self.service.flush()
        self.assertEqual(len(self.first.posts),posts);self.assertTrue(slack_workflow.pending(self.store,self.tid))

    def test_lost_send_response_is_reconciled_without_second_dm(self):
        self.first.lose_reply=True
        row=self.handoff();self.assertEqual(row['ts'],'')
        self.first.lose_reply=False
        with self.store.connect() as c:c.execute('UPDATE outbox SET available_at=0')
        before=len(self.first.posts);self.service.flush();self.assertEqual(len(self.first.posts),before)
        with self.store.connect() as c:row=dict(c.execute('SELECT * FROM slack_direct').fetchone())
        self.assertTrue(row['ts']);self.echo(row);self.service.flush()
        self.assertFalse(slack_workflow.pending(self.store,self.tid))

    def test_restart_retains_unreceived_dm_and_does_not_publish_twice(self):
        row=self.handoff();count=len(self.first.posts)
        replacement=SlackService(self.store);replacement.config=self.config;replacement.web=self.service.web;replacement.bot_user_ids=self.service.bot_user_ids;self.service=replacement
        self.service.flush();self.assertEqual(len(self.first.posts),count)
        self.assertTrue(slack_workflow.pending(self.store,self.tid));self.echo(row);self.service.flush()
        self.assertFalse(slack_workflow.pending(self.store,self.tid))

    def test_shared_or_reused_bot_cannot_enable_personal_dm(self):
        config=copy.deepcopy(self.config)
        config['connections']=[{'id':'shared','agent_id':'shared','bot_token':'xoxb-one','app_token':'xapp-one'}]
        with self.assertRaisesRegex(ValueError,'별도 봇'):prepare_slack(self.store,config)
        config=copy.deepcopy(self.config)
        for c in config['connections']:c.update(bot_token='xoxb-same',app_token='xapp-same')
        with self.assertRaisesRegex(ValueError,'서로 다른'):prepare_slack(self.store,config)

    def test_platform_dm_restriction_is_saved_as_capability_not_auth_failure(self):
        class Denied(Exception):response={'error':'cannot_dm_bot'}
        class Probe:
            def __init__(self,token):self.token=token
            def auth_test(self):return {'ok':True,'team_id':'TTEAM123','user_id':'U'+self.token[-1]}
            def conversations_info(self,**kw):return {'channel':{'name':'moa-team'}}
            def conversations_members(self,**kw):return {'members':['U1','U2']}
            def apps_connections_open(self,*,app_token):return {'url':'wss://fixture.invalid'}
            def conversations_open(self,**kw):raise Denied()
        config=copy.deepcopy(self.config)
        for i,c in enumerate(config['connections'],1):c.update(bot_token='xoxb-'+str(i),app_token='xapp-'+str(i))
        with self.assertRaisesRegex(ValueError,'cannot_dm_bot'):test_connection(self.store,config,Probe)
        from setup_config import read_setting
        self.assertEqual(read_setting(self.store,'slack_dm_capability',{})['state'],'unsupported')

    def test_dm_requester_receives_reply_from_each_teammates_own_dm(self):
        with self.store.connect() as c:
            c.execute('UPDATE slack_threads SET channel=?,thread_ts=? WHERE task_id=?',('DUSERFIRST','9.1',self.tid))
            c.execute('INSERT INTO slack_requesters VALUES(?,?)',(self.tid,'U12345678'))
        self.store.deliver(self.tid,'checker','user',{'summary':'검증 완료'})
        self.service.flush();self.assertEqual(self.second.opens[-1]['users'],'U12345678')
        self.assertTrue(any(p['channel']=='DPEERS123' for p in self.second.posts))
        self.assertEqual(self.second.posts[-1]['channel'],'C12345678')
        dm=next(p for p in self.second.messages if p['channel']=='DPEERS123')
        reply={'event_id':'EvUserReply','event':{'type':'message','channel_type':'im','channel':'DPEERS123',
               'user':'U12345678','text':'추가로 파일을 확인해 주세요.','ts':'500.1','thread_ts':dm['ts']}}
        self.assertEqual(self.service.receive(reply,self.config['connections'][1]),self.tid)
        self.assertEqual(len(self.store.snapshot()['tasks']),1)


if __name__=='__main__':unittest.main()
