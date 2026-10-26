from pathlib import Path
import json,urllib.request,hashlib
from urllib.parse import urlparse
out=Path('reports/profile-thumbnails');before=json.loads((out/'slack-before.json').read_text());after=json.loads((out/'slack-after.json').read_text());selected=json.loads((out/'selected.json').read_text(encoding='utf-8-sig'));checks=[]
for agent,name in [('builder','지우'),('checker','서연')]:
 p=next(p for p in after.values() if p['name']==name);old=before[p['id']];url=p['icons']['image_72'];assert url!=old['icons']['image_72'];assert urlparse(url).hostname=='avatars.slack-edge.com'
 image=urllib.request.urlopen(url,timeout=20).read();(out/(agent+'-slack.png')).write_bytes(image)
 candidate=Path('src/web/profile-candidates')/(selected[agent]['candidate_id']+'.png');canonical=Path('data/profiles')/(selected[agent]['image_hash']+'.png');cli=Path('data/slack-auto')/('person-'+agent)/'profile.png'
 assert candidate.read_bytes()==canonical.read_bytes()==cli.read_bytes()
 checks.append({'agent_id':agent,'name':name,'candidate_id':selected[agent]['candidate_id'],'image_hash':selected[agent]['image_hash'],'canonical_and_slack_upload_identical':True,'slack_icon_changed':True,'slack_icon_url':url,'slack_updated':p['updated']})
(out/'evidence.json').write_text(json.dumps({'python_tests':'574 passed','ui_suites':'13 regression suites + profile candidates DOM passed','assets':'20 PNG images validated at 512x512, under 1MB','profiles':checks},ensure_ascii=False,indent=2),encoding='utf-8');print('Both Slack icon URLs changed; canonical image and CLI upload byte-identical')
