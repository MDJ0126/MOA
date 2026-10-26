import json,sys
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()))
from app import Store
from setup_config import slack_settings
from slack_service import WebClient
out=Path('reports/automatic-thumbnails');out.mkdir(exist_ok=True,parents=True)
result={}
for conn in slack_settings(Store('data'),secrets=True)['connections']:
 if conn['agent_id'] not in ('planner','reporter'):continue
 client=WebClient(token=conn['bot_token'],timeout=20);auth=client.auth_test()
 channel=slack_settings(Store('data'))['channel']
 messages=client.conversations_history(channel=channel,limit=100)['messages']
 bot=next((m['bot_profile'] for m in messages if m.get('bot_id')==auth['bot_id'] and m.get('bot_profile')),None)
 if bot is None:
  posted=client.chat_postMessage(channel=channel,text='[MOA 프로필 QA] 팀원 썸네일 자동 반영을 확인 중입니다.')
  bot=posted['message'].get('bot_profile')
  if not bot:bot=next(m['bot_profile'] for m in client.conversations_history(channel=channel,limit=10)['messages'] if m.get('bot_id')==auth['bot_id'] and m.get('bot_profile'))
 result[conn['agent_id']]={k:bot.get(k) for k in ('id','name','icons','updated')}
(out/(sys.argv[1]+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print('Verified profiles:',list(result))
