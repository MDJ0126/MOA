import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import Store
from setup_config import PRESETS,save_team
import office


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent)
        self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.team=save_team(self.store,PRESETS[1])
        self.agent=self.team['agents'][0]['id']
        self.config={'enabled':True,'allowed_users':['UOWNER123'],'connections':[{'agent_id':self.agent,'bot_token':'test-only'}]}

    def test_all_twenty_activities_persist_with_directional_destinations(self):
        from office_activities import OFFICE_ACTIVITY_IDS
        self.assertEqual(len(set(OFFICE_ACTIVITY_IDS)),20)
        for activity in OFFICE_ACTIVITY_IDS:
            value={'version':1,'agents':[[self.agent,{'mode':'activity','phase':'perform','activity':activity,'until':123456,'dest':{'x':.2,'y':.1},'round':1,'position':{'x':-40,'y':70}}]]}
            result=office.save_life(self.store,value)['agents'][0][1]
            self.assertEqual(result['activity'],activity);self.assertEqual(result['dest']['y'],.1);self.assertEqual(result['position']['x'],-40)
            self.assertEqual(office.life(Store(self.temp.name))['agents'][0][1]['activity'],activity)
        with self.assertRaises(ValueError):office.save_life(self.store,{'version':1,'agents':[[self.agent,{'mode':'activity','phase':'perform','activity':'unknown'}]]})

    def test_appearance_survives_store_reload(self):
        value={'hair':3,'skin':2,'shirt':5,'accessory':1}
        office.save(self.store,{'agent_id':self.agent,'appearance':value})
        self.assertEqual(office.appearance(Store(self.temp.name))[self.agent],value)

    def test_invalid_parts_or_agent_do_not_overwrite_settings(self):
        for value in ({'hair':True,'skin':0,'shirt':0,'accessory':0},{'hair':4,'skin':0,'shirt':0,'accessory':0},{'hair':0}):
            with self.assertRaises(ValueError):office.save(self.store,{'agent_id':self.agent,'appearance':value})
        with self.assertRaises(ValueError):office.save(self.store,{'agent_id':'missing','appearance':{}})
        self.assertEqual(office.appearance(self.store),{})

    def test_life_survives_reload_and_validates_destinations(self):
        value={'version':1,'agents':[[self.agent,{'mode':'coffee','phase':'brew','until':123456,'dest':{'coffee':True},'cup':False}]]}
        self.assertEqual(office.save_life(self.store,value),value)
        self.assertEqual(office.life(Store(self.temp.name)),value)
        bad={'version':1,'agents':[[self.agent,{'mode':'walk','phase':'go','until':1,'dest':{'x':100},'cup':False}]]}
        with self.assertRaises(ValueError):office.save_life(self.store,bad)
        self.assertEqual(office.life(self.store),value)

    def test_company_identity_persists_and_randomizes(self):
        import io
        from PIL import Image
        value=office.company(self.store,'테스트')
        self.assertEqual(value['name'],'테스트 컴퍼니')
        icon=office.company_icon(self.store)
        self.assertEqual(Image.open(io.BytesIO(icon)).size,(512,512))
        self.assertEqual(office.company_icon(Store(self.temp.name)),icon)
        office.save_company(self.store,{'name':'우리 회사'})
        self.assertEqual(office.company(self.store,'다른 이름')['name'],'우리 회사')
        office.save_company(self.store,{'randomize':True})
        self.assertNotEqual(office.company(self.store)['icon_url'],value['icon_url'])
        self.assertEqual(office.company(self.store)['name'],'우리 회사')
        office.save_company(self.store,{'name':''})
        self.assertEqual(office.company(self.store,'새 이름')['name'],'새 이름 컴퍼니')

    def test_company_upload_is_normalized_and_invalid_upload_preserves_it(self):
        import base64,io
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(100,200),'red').save(out,format='PNG')
        office.save_company(self.store,{'image_base64':base64.b64encode(out.getvalue()).decode()})
        icon=office.company_icon(self.store)
        self.assertEqual(Image.open(io.BytesIO(icon)).size,(512,512))
        with self.assertRaises(ValueError):office.save_company(self.store,{'image_base64':'not an image'})
        self.assertEqual(office.company_icon(self.store),icon)

    def test_dm_uses_selected_bot_and_allowed_human(self):
        calls=[]
        class Client:
            def auth_test(self):return {'team_id':'TTEAM123'}
            def conversations_open(self,**kw):calls.append(kw);return {'channel':{'id':'DDIRECT123'}}
        with patch.object(office,'slack_settings',return_value=self.config):
            result=office.direct_message(self.store,{'agent_id':self.agent},lambda token:(calls.append(token) or Client()))
        self.assertEqual(calls,['test-only',{'users':'UOWNER123'}])
        self.assertEqual(result['url'],'https://app.slack.com/client/TTEAM123/DDIRECT123')
        self.assertNotIn('token',str(result))

    def test_multiple_users_require_explicit_selection_and_reject_unknown(self):
        self.config['allowed_users'].append('UOTHER123')
        with patch.object(office,'slack_settings',return_value=self.config):
            result=office.direct_message(self.store,{'agent_id':self.agent})
            self.assertEqual(result,{'users':['UOWNER123','UOTHER123']})
            with self.assertRaises(ValueError):office.direct_message(self.store,{'agent_id':self.agent,'user_id':'UATTACK123'})

    def test_missing_bot_rejected_without_falling_back_to_other_teammate(self):
        with patch.object(office,'slack_settings',return_value=self.config):
            with self.assertRaises(ValueError):office.direct_message(self.store,{'agent_id':self.team['agents'][1]['id']})

    def test_slack_failure_never_leaks_credentials(self):
        class Client:
            def auth_test(self):raise RuntimeError('test-only-secret')
        with patch.object(office,'slack_settings',return_value=self.config):
            with self.assertRaisesRegex(ValueError,'RuntimeError') as caught:office.direct_message(self.store,{'agent_id':self.agent},lambda token:Client())
        self.assertNotIn('test-only',str(caught.exception))


if __name__=='__main__':unittest.main()
