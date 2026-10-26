"""Collect pinned Devicon SVGs locally; create MOA category icons for skills."""
from pathlib import Path
import json
import urllib.request

root=Path(__file__).resolve().parent.parent/'src/web/resource-icons';root.mkdir(parents=True,exist_ok=True)
version='v2.16.0'
names={'python':'python/python-original.svg','git':'git/git-original.svg','nodejs':'nodejs/nodejs-original.svg','vscode':'vscode/vscode-original.svg','blender':'blender/blender-original.svg','gimp':'gimp/gimp-original.svg','inkscape':'inkscape/inkscape-original.svg','slack':'slack/slack-original.svg'}
names.update(unrealengine='unrealengine/unrealengine-original.svg',unity='unity/unity-original.svg',visualstudio='visualstudio/visualstudio-plain.svg',chrome='chrome/chrome-original.svg')
records=[]
for key,name in names.items():
    url=f'https://raw.githubusercontent.com/devicons/devicon/{version}/icons/{name}'
    try:
        body=urllib.request.urlopen(url,timeout=20).read(200000)
        if b'<svg' not in body or b'<script' in body:raise ValueError('Invalid SVG')
        (root/(key+'.svg')).write_bytes(body);records.append({'id':key,'source':url,'license':'Devicon MIT; respective brand trademarks apply'})
        print('collected',key)
    except Exception as error:print('icon fetch failed',key,type(error).__name__)
paths={'archive':'M5 8h22v20H5zM3 3h26v6H3zm9 10h8v5h-8z','ffmpeg':'M5 4h22v24H5zM10 9l13 7-13 7z','design':'M6 24L22 4l6 5-17 20H4z','quality':'M16 3l12 5v8c0 7-12 14-12 14S4 23 4 16V8zM9 16l5 5 9-10','image':'M4 5h24v23H4zM5 24l8-10 6 7 4-5 5 8M24 10h.1','research':'M22 22l8 8M25 14A11 11 0 1 1 3 14a11 11 0 0 1 22 0','skill':'M16 2l4 9 10 5-10 4-4 10-5-10-9-4 9-5z'}
for key,path in paths.items():
    (root/(key+'.svg')).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="{path}" fill="none" stroke="#b86a50" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',encoding='utf-8')
    records.append({'id':key,'source':'MOA original vector icon','license':'project asset'})
# Visible fallback if a vendor path was removed from the pinned release.
for key in names:
    if not (root/(key+'.svg')).exists():(root/(key+'.svg')).write_text((root/'skill.svg').read_text(),encoding='utf-8')
(root/'sources.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
license_url=f'https://raw.githubusercontent.com/devicons/devicon/{version}/LICENSE'
(root/'DEVICON-LICENSE.txt').write_bytes(urllib.request.urlopen(license_url,timeout=20).read(30000))
