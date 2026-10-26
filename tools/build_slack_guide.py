"""Named teammate onboarding shared by the portable report and in-app guide."""
import html
import json
from pathlib import Path
import sys
from urllib.parse import quote

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'src'))
from setup_config import manifest


def onboarding():
    links=[]
    for key,name in [('jiwoo','Jiwoo'),('seoyeon','Seoyeon')]:
        data=manifest(name)
        (ROOT/'docs/slack-apps'/f'{key}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        url='https://api.slack.com/apps?new_app=1&manifest_json='+quote(json.dumps(data,ensure_ascii=False,separators=(',',':')),safe='')
        links.append(f'<a class="button" href="{html.escape(url,quote=True)}" target="_blank" rel="noreferrer">{name} 팀원 봇 만들기 ↗</a>')
    return (ROOT/'docs/slack-bot-guide.html').read_text(encoding='utf-8').replace('{{PERSON_LINKS}}',''.join(links))


def update():
    content=onboarding()
    import re
    for name in ['connect.html','index.html']:
        path=ROOT/'reports/slack-vm-implementation'/name
        text=path.read_text(encoding='utf-8')
        text,count=re.subn(r'<section id="connect">.*?</section>',lambda _:content,text,count=1,flags=re.S)
        if count!=1:raise ValueError('onboarding section missing')
        path.write_text(text,encoding='utf-8')
    # Links to local files are useful in the portable guide. In-app docs use the
    # prefilled buttons; don't show file links unavailable through the API.
    app_content=re.sub(r'<p><a href="../../docs/slack-apps/jiwoo.json">.*?</p>','',content)
    (ROOT/'docs/slack.html').write_text('<h1>팀원 봇 만들기</h1>'+app_content,encoding='utf-8')


if __name__=='__main__':update()
