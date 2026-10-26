import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import Store
from slack_auto import SlackAuto

class ProfileSyncTests(unittest.TestCase):
 def test_visible_korean_name_and_private_handle_preserve_existing_permissions(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as folder:
   auto=SlackAuto(Store(folder));auto.folder.mkdir(parents=True)
   (auto.folder/'credentials.json').write_text(json.dumps({'TTEST123':{'token':'xoxe.fixture'}}))
   project=auto.folder/'person-builder';(project/'.slack').mkdir(parents=True)
   (project/'.slack/apps.dev.json').write_text(json.dumps({'TTEST123':{'app_id':'ATEST123'}}))
   remote={'display_information':{'name':'old'},'features':{'bot_user':{'display_name':'old'}},'oauth_config':{'scopes':{'bot':['chat:write']}},'settings':{'socket_mode_enabled':True}}
   calls=[]
   class Client:
    def __init__(self,**kw):pass
    def apps_manifest_export(self,**kw):return {'manifest':copy.deepcopy(remote)}
    def apps_manifest_update(self,**kw):
     value=json.loads(kw['manifest']);calls.append(value);remote.clear();remote.update(value);return {'permissions_updated':False}
   with patch('slack_service.WebClient',Client):
    result=auto.normalize_remote_profile({'id':'builder','name':'지우'},{'team_id':'TTEST123'},project)
   self.assertEqual(result,{'name':'지우'});self.assertEqual(calls[0]['features']['bot_user']['display_name'],'old')
   calls.clear()
   with patch('slack_service.WebClient',Client):auto.normalize_remote_profile({'id':'builder','name':'지우'},{'team_id':'TTEST123'},project)
   self.assertEqual(calls,[], 'Matching app name must not rewrite the workspace bot identity')
   self.assertEqual(remote['oauth_config']['scopes']['bot'],['chat:write']);self.assertTrue(remote['settings']['socket_mode_enabled'])

if __name__=='__main__':unittest.main()
