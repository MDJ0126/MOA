"""Official Slack CLI hook: transfer provided bot credentials through DPAPI only."""
import base64
import json
import os
from pathlib import Path
import sys
from setup_config import protect


def main():
    action=sys.argv[1]
    if action=='manifest':
        print(Path('manifest.json').read_text(encoding='utf-8'));return
    if action=='start':
        bot=os.environ.get('SLACK_BOT_TOKEN') or os.environ.get('SLACK_CLI_XOXB','')
        app=os.environ.get('SLACK_APP_TOKEN') or os.environ.get('SLACK_CLI_XAPP','')
        if not bot.startswith('xoxb-') or not app.startswith('xapp-'):raise ValueError('Slack CLI credential envelope missing')
        data=base64.b64encode(protect(json.dumps({'bot_token':bot,'app_token':app}).encode()))
        path=Path('registration.bin');temporary=path.with_suffix('.tmp')
        temporary.write_bytes(data);temporary.replace(path)
        print('MOA_CREDENTIALS_READY');return
    if action=='hooks':
        import subprocess
        command=lambda verb:subprocess.list2cmdline([sys.executable,str(Path(__file__).resolve()),verb])
        print(json.dumps({'runtime':'python','hooks':{'get-manifest':command('manifest'),'start':command('start')},
                          'config':{'sdk-managed-connection-enabled':True}}));return
    raise ValueError('Unsupported hook')


if __name__=='__main__':main()
