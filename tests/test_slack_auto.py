import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Store
from setup_config import PRESETS,save_team,slack_settings,protect
from slack_auto import SlackAuto,clean,redact


class Client:
    def __init__(self,token,timeout=15):self.token=token
    def auth_test(self):return {'ok':True,'team_id':'TTEST123','user_id':'UBOT'+self.token[-6:].upper()}
    def conversations_create(self,**kw):return {'channel':{'id':'CTEAM123'}}
    def conversations_join(self,**kw):return {'ok':True}


class AutoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);save_team(self.store,PRESETS[1]);self.auto=SlackAuto(self.store)
        self.normalizer=patch.object(self.auto,'normalize_remote_profile',side_effect=lambda a,*args:{'name':a['name']});self.normalizer.start();self.addCleanup(self.normalizer.stop)
        self.auto.folder.mkdir()

    def fake_run(self,args,cwd=None,timeout=45,ready=None,env=None):
        if args[:2]==['auth','list']:return 'Workspace (Team ID: TTEST123)\nUser ID: UOWNER123\n'
        self.assertEqual(args[0],'run');self.assertIn('--app',args)
        self.assertTrue(Path(env['SLACK_CLI_APP_ICON_PATH']).is_file())
        tag='jiwoo1' if 'builder' in str(cwd) else 'seoyeon2'
        credentials={'bot_token':'xoxb-'+tag,'app_token':'xapp-'+tag}
        (Path(cwd)/'registration.bin').write_bytes(base64.b64encode(protect(json.dumps(credentials).encode())))
        return 'MOA_CREDENTIALS_READY'

    def test_first_authorization_returns_only_one_time_command(self):
        with patch.object(self.auto,'prepare'),patch.object(self.auto,'run',return_value='/slackauthticket AUTH_TICKET123'):
            self.auto.work('login',{})
        status=self.auto.snapshot();self.assertEqual(status['phase'],'awaiting_approval')
        self.assertEqual(status['auth_command'],'/slackauthticket AUTH_TICKET123')
        self.assertNotIn('bot_token',json.dumps(status));self.assertNotIn('app_token',json.dumps(status))

    def test_successful_authentication_clears_ticket_and_saves_no_code(self):
        self.auto.ticket='AUTH';self.auto.ticket_until=10**12
        with patch.object(self.auto,'prepare'),patch.object(self.auto,'run',return_value='ok'),patch.object(self.auto,'accounts',return_value=[{'team_id':'TTEST123','user_id':'UOWNER123'}]):
            self.auto.work('challenge',{'challenge':'abcd1234'})
        self.assertEqual(self.auto.snapshot()['phase'],'authorized');self.assertNotIn('auth_command',self.auto.snapshot())
        self.assertNotIn('abcd1234',json.dumps(self.auto.snapshot()))

    def test_named_bot_registration_imports_credentials_and_enables_only_complete_team(self):
        with patch.object(self.auto,'run',side_effect=self.fake_run),patch('slack_service.WebClient',Client),patch('slack_service.test_connection',return_value={'ok':True}) as check:
            self.auto.add({'agent_id':'builder','name':'지우'})
            saved=slack_settings(self.store);self.assertFalse(saved['enabled']);self.assertFalse(saved['team_dm'])
            self.assertTrue(saved['connections'][0]['has_bot_token']);self.assertEqual(saved['allowed_users'],['UOWNER123'])
            self.auto.add({'agent_id':'checker','name':'서연'})
            saved=slack_settings(self.store);self.assertTrue(saved['enabled']);self.assertEqual(len(saved['connections']),2)
            check.assert_called_once()
        self.assertNotIn('xoxb-',json.dumps(self.auto.snapshot()))
        self.assertFalse(list(self.auto.folder.rglob('registration.bin')))
        for key in ['builder','checker']:
            project=self.auto.folder/('person-'+key)
            data=json.loads((project/'manifest.json').read_text(encoding='utf-8'))
            self.assertIn('im:write',data['oauth_config']['scopes']['bot'])
            self.assertNotIn('빌더',data['display_information']['name'])

    def test_failed_dm_probe_preserves_imported_tokens_without_enabling(self):
        with patch.object(self.auto,'run',side_effect=self.fake_run),patch('slack_service.WebClient',Client),patch('slack_service.test_connection',side_effect=ValueError('DM denied')):
            self.auto.add({'agent_id':'builder'})
            with self.assertRaisesRegex(ValueError,'DM denied'):self.auto.add({'agent_id':'checker'})
        saved=slack_settings(self.store);self.assertFalse(saved['enabled'])
        self.assertEqual(len(saved['connections']),2);self.assertTrue(all(c['has_bot_token'] for c in saved['connections']))

    def test_reregistering_one_person_reuses_project_and_connection(self):
        with patch.object(self.auto,'run',side_effect=self.fake_run),patch('slack_service.WebClient',Client):
            self.auto.add({'agent_id':'builder'});self.auto.add({'agent_id':'builder','name':'Jiwoo'})
        self.assertEqual(len(slack_settings(self.store)['connections']),1)
        self.assertEqual(len(list(self.auto.folder.glob('person-*'))),1)

    def test_rejects_unknown_person_and_bad_picture_before_creating_app(self):
        with patch.object(self.auto,'accounts',return_value=[{'team_id':'TTEST123','user_id':'UOWNER123'}]),patch.object(self.auto,'run') as run:
            with self.assertRaises(ValueError):self.auto.add({'agent_id':'../../elsewhere'})
            with self.assertRaisesRegex(ValueError,'PNG'):self.auto.add({'agent_id':'builder','image_base64':base64.b64encode(b'not an image').decode()})
            run.assert_not_called()

    def test_busy_double_click_is_rejected(self):
        self.auto.busy=True
        with self.assertRaisesRegex(ValueError,'진행 중'):self.auto.dispatch('add',{'agent_id':'builder'})

    def test_cli_output_is_redacted_and_escape_sequences_removed(self):
        text='\x1b[31mOops xoxb-sensitive-value xapp-private-value\x1b[0m'
        safe=redact(text);self.assertNotIn('sensitive',safe);self.assertNotIn('private-value',safe);self.assertNotIn('\x1b',safe)

    def test_canceled_or_expired_auth_cannot_connect(self):
        self.auto.ticket='OLD';self.auto.ticket_until=1
        with patch.object(self.auto,'prepare'),patch.object(self.auto,'run') as run:self.auto.work('challenge',{'challenge':'test1234'});run.assert_not_called()
        self.assertEqual(self.auto.snapshot()['phase'],'error')

    def test_add_button_starts_initial_auth_and_continues_after_approval(self):
        with patch.object(self.auto,'accounts',return_value=[]),patch.object(self.auto,'run',return_value='/slackauthticket ONE_TIME123'):
            self.auto.add({'agent_id':'builder','name':'지우'})
        self.assertEqual(self.auto.snapshot()['phase'],'awaiting_approval')
        self.assertEqual(self.auto.pending_add['agent_id'],'builder')
        with patch.object(self.auto,'prepare'),patch.object(self.auto,'run',return_value='ok'),patch.object(self.auto,'accounts',return_value=[{'team_id':'TTEST123','user_id':'UOWNER123'}]),patch.object(self.auto,'add') as add:
            self.auto.work('challenge',{'challenge':'code1234'})
            add.assert_called_once_with({'agent_id':'builder','name':'지우'})
        self.assertIsNone(self.auto.pending_add)

    def test_cli_start_hook_imports_only_encrypted_environment_credentials(self):
        import os,subprocess,sys
        project=Path(self.temp.name)/'hook';project.mkdir()
        env={**os.environ,'SLACK_APP_TOKEN':'xapp-fixture-secret','SLACK_BOT_TOKEN':'xoxb-fixture-secret'}
        result=subprocess.run([sys.executable,str(Path(__file__).resolve().parent.parent/'src/slack_cli_hook.py'),'start'],cwd=project,env=env,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        saved=(project/'registration.bin').read_bytes()
        self.assertNotIn(b'xapp-',saved);self.assertNotIn(b'xoxb-',saved)
        decoded=json.loads(protect(base64.b64decode(saved),True))
        self.assertEqual(decoded['bot_token'],'xoxb-fixture-secret')
        self.assertNotIn(b'xoxb-',result.stdout)


if __name__=='__main__':unittest.main()
