"""Interactive local Telegram setup. Credentials are never printed or passed in args."""
import getpass
import json
from pathlib import Path
import re
import sys

import httpx
from runtime import ROOT
from setup_project import write_json

def main():
    if (ROOT/'config/telegram-bridge.json').exists():
        raise ValueError('Bridge already configured. Inspect existing settings; do not replace its session or owner silently.')
    token=getpass.getpass('Paste the BotFather token here (hidden): ').strip()
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+',token):raise ValueError('Token format is invalid. Copy the full BotFather token.')
    base='https://api.telegram.org/bot'+token+'/'
    with httpx.Client(timeout=20,trust_env=False) as client:
        def tg(method):
            r=client.post(base+method,json={});d=r.json()
            if r.status_code!=200 or not d.get('ok'):raise ValueError('Telegram rejected setup. Check the token or network; credentials were not logged.')
            return d['result']
        me=tg('getMe')
        if tg('getWebhookInfo').get('url'):raise ValueError('Bot has an existing webhook. Use a new bot; setup will not take over.')
        print('Open https://t.me/'+me['username']+' in Telegram and send /start.')
        input('Press Enter after sending /start: ')
        updates=tg('getUpdates')
        people={}
        for u in updates:
            msg=u.get('message',{});chat=msg.get('chat',{});sender=msg.get('from',{})
            if chat.get('type')=='private' and sender.get('id')==chat.get('id'):
                people[chat['id']]=sender
        if len(people)!=1:raise ValueError('Could not identify exactly one private owner. Use a fresh personal bot and send /start, then retry.')
        chat,sender=next(iter(people.items()))
        print('Detected private owner:',sender.get('username') or sender.get('first_name'),'ID',chat)
        if input('Is this you? Type yes: ').strip().lower()!='yes':raise ValueError('No configuration saved.')
        url=input('Local Kimi URL [http://127.0.0.1:58627]: ').strip() or 'http://127.0.0.1:58627'
        from urllib.parse import urlparse
        if urlparse(url).hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('Kimi must be local.')
        token_path=Path(input('Kimi token file [~/.kimi-code/server.token]: ').strip() or '~/.kimi-code/server.token').expanduser()
        bearer=token_path.read_text(encoding='utf-8').strip()
        def kimi(path):
            r=client.get(url.rstrip('/')+'/api/v1'+path,headers={'Authorization':'Bearer '+bearer})
            if r.status_code!=200:raise ValueError('Kimi API unavailable or incompatible; check server and token file.')
            data=r.json()
            if data.get('code')!=0:raise ValueError('Kimi API rejected the request.')
            return data['data']
        sessions=[s for s in kimi('/sessions?page_size=100')['items'] if Path(s.get('cwd','')).resolve()==ROOT]
        if not sessions:raise ValueError('Open a Kimi web session in this project first, then retry.')
        for i,s in enumerate(sessions):print(i+1,s['id'] if 'id' in s else s.get('session_id'))
        n=int(input('Choose the session number: '))-1
        if not 0<=n<len(sessions):raise ValueError('Invalid session number.')
        s=sessions[n];sid=s.get('id') or s['session_id']
        kimi(f'/sessions/{sid}/agents/main/turns')  # Confirm canonical turn protocol exists.
        (ROOT/'secrets').mkdir(exist_ok=True)
        path=ROOT/'secrets/telegram-bot-token';path.write_text(token, encoding='utf-8');path.chmod(0o600)
        (ROOT/'config/telegram.yaml').write_text(f'chat_id: {chat}\ntoken_file: secrets/telegram-bot-token\n', encoding='utf-8')
        write_json(ROOT/'config/telegram-bridge.json',{'workspace':str(ROOT),'kimi_url':url,
            'kimi_token_file':str(token_path),'session_id':sid,'allowed_user_id':chat})
        print('Configured. Start telegram_bridge.py, then test /status and a harmless text reply. No hunt started.')

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,httpx.HTTPError,KeyError) as e:
        # HTTP exceptions may contain the bot URL/token: never print them.
        print(str(e) if isinstance(e,ValueError) else 'Setup could not finish. Check connectivity, file paths and Kimi compatibility.',file=sys.stderr)
        sys.exit(1)
