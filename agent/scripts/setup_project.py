"""Idempotent local setup; preserves user files and unrelated client settings."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import tomli_w

from runtime import ROOT, browser_endpoint

SERVERS = ['linkedin', 'remotive', 'weworkremotely', 'greenhouse']
BROWSER_VERSION = '0.0.86'

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')

def backup(path):
    if path.exists():
        dest = ROOT/'data/setup-backups'/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(path, dest)

def merge_json(path, values):
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    for section, entries in values.items():
        data.setdefault(section, {}).update(entries)
    backup(path)
    write_json(path, data)

def config_entries(outlook=False):
    python = ROOT/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    names = SERVERS + (['outlook'] if outlook else [])
    entries = {n: {'command':str(python), 'args':[str(ROOT/'agent/scripts'/f'{n}_mcp.py')],
                   'env':{'PYTHONUTF8':'1'}} for n in names}
    node = shutil.which('node')
    if not node:
        raise ValueError('Install Node.js LTS from nodejs.org, reopen your terminal, then retry.')
    cli = ROOT/'data/browser-mcp/node_modules/@playwright/mcp/cli.js'
    if not cli.exists():
        raise ValueError('Browser MCP package is missing. Run setup first.')
    entries['playwright'] = {'command':node,'args':[str(cli),'--cdp-endpoint',browser_endpoint()]}
    return entries

def configure(outlook=False):
    outlook = outlook or (ROOT/'config/outlook.json').exists()
    entries = config_entries(outlook)
    merge_json(ROOT/'.kimi-code/mcp.json', {'mcpServers':{
        n:{**v, 'startupTimeoutMs':60000,'toolTimeoutMs':7200000} for n,v in entries.items()}})
    merge_json(ROOT/'.mcp.json', {'mcpServers':{
        n:{**v, 'timeout':7200000} for n,v in entries.items()}})
    # Client process settings, not server env: a backgrounded call still owns Chrome.
    merge_json(ROOT/'.claude/settings.local.json', {'env':{
        'MCP_TIMEOUT':'60000','MCP_TOOL_TIMEOUT':'7200000',
        'CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT':'7200000',
        'CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS':'0'}})
    path = ROOT/'.codex/config.toml'
    data = tomllib.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    data.setdefault('mcp_servers',{}).update({n:{**v,'startup_timeout_sec':60,'tool_timeout_sec':7200} for n,v in entries.items()})
    backup(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(tomli_w.dumps(data),encoding='utf-8')
    write_json(ROOT/'data/mcp-commands.json', entries)
    print('Project MCP configuration written for Kimi Code, Codex and Claude Code. Global settings untouched.')

def install(outlook=False):
    for name in ['data','logs','runs','ledger','secrets','profile/cv','config']:
        (ROOT/name).mkdir(parents=True,exist_ok=True)
    for source in (ROOT/'templates').glob('*'):
        if source.name.endswith('.example.json'):
            target = ROOT/'config'/source.name.replace('.example','')
            if not target.exists(): shutil.copy2(source,target)
    for name in ['identity.yaml','preferences.yaml','targets.yaml','qa-bank.md']:
        source = ROOT/'templates'/name
        target = ROOT/('profile' if name in ['identity.yaml','qa-bank.md'] else 'config')/name
        if not target.exists(): shutil.copy2(source,target)
    npm = shutil.which('npm')
    if not npm: raise ValueError('Install Node.js LTS from nodejs.org; reopen the terminal and rerun setup.')
    prefix = ROOT/'data/browser-mcp'
    manifest = prefix/'node_modules/@playwright/mcp/package.json'
    installed = json.loads(manifest.read_text(encoding='utf-8')).get('version') if manifest.exists() else None
    if installed != BROWSER_VERSION:
        command = [npm,'install','--prefix',str(prefix),'--no-audit','--no-fund',f'@playwright/mcp@{BROWSER_VERSION}']
        if os.name == 'nt':
            # Run npm through Node directly: paths with & must never become shell code.
            cli = Path(npm).parent/'node_modules/npm/bin/npm-cli.js'
            node = shutil.which('node')
            if not node or not cli.is_file():
                raise ValueError('Cannot locate npm-cli.js. Repair the Node.js LTS installation, then retry.')
            command = [node, str(cli), *command[1:]]
        subprocess.run(command,check=True)
    configure(outlook)
    print('Next: complete docs/onboarding.md, then run doctor. No browser, hunt or schedule was started.')

async def check_tools():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    configs = json.loads((ROOT/'data/mcp-commands.json').read_text(encoding='utf-8'))
    results=[]
    for name, c in configs.items():
        try:
            async with asyncio.timeout(60):
                params=StdioServerParameters(command=c['command'],args=c['args'],env={**os.environ,**c.get('env',{})})
                async with stdio_client(params) as (r,w):
                    async with ClientSession(r,w) as session:
                        await session.initialize()
                        tools=(await session.list_tools()).tools
                        results.append({'server':name,'status':'ok','tools':[t.name for t in tools]})
        except Exception as e:
            results.append({'server':name,'status':'error','reason':type(e).__name__})
    print(json.dumps(results,indent=2))
    return all(r['status']=='ok' for r in results)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['install','configure','check-tools'],nargs='?',default='install')
    p.add_argument('--outlook',action='store_true',help='Register optional Outlook tools; does not log in or read mail.')
    a=p.parse_args()
    try:
        if a.action=='check-tools':
            if not asyncio.run(check_tools()):sys.exit(1)
        elif a.action=='configure':configure(a.outlook)
        else:install(a.outlook)
    except (ValueError,subprocess.CalledProcessError) as e:
        raise SystemExit(str(e)) from None

if __name__=='__main__':main()
