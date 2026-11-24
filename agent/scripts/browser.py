"""Open the dedicated visible Chrome profile; never control the everyday profile."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import urlopen

import psutil
from runtime import ROOT, browser_endpoint, settings

PROFILE = ROOT/'secrets/browser-profile'
STATE = ROOT/'data/browser.json'
LOGIN = {'linkedin':'https://www.linkedin.com/login', 'blank':'about:blank'}

def executable():
    configured = settings().get('chrome_path')
    candidates = [configured] if configured else []
    if sys.platform == 'darwin':
        candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                       str(Path.home()/'Applications/Google Chrome.app/Contents/MacOS/Google Chrome')]
    elif os.name == 'nt':
        candidates += [str(Path(os.environ.get(base,''))/'Google/Chrome/Application/chrome.exe')
                       for base in ['PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'] if os.environ.get(base)]
    else:
        candidates += [shutil.which('google-chrome'),shutil.which('chromium')]
    for value in candidates:
        if value and Path(value).is_file():return str(Path(value).resolve())
    raise ValueError('Google Chrome not found. Install Chrome or set chrome_path in config/runtime.json.')

def probe():
    try:
        with urlopen(browser_endpoint()+'/json/version',timeout=2) as r:return json.load(r)
    except (OSError,ValueError):return None

def owned_process():
    if not STATE.exists():return None
    try:
        saved=json.loads(STATE.read_text(encoding='utf-8'));p=psutil.Process(saved['pid'])
        args=p.cmdline()
        if abs(p.create_time()-saved['created'])<0.01 and f'--user-data-dir={PROFILE}' in args:
            return p
    except (psutil.Error,ValueError,KeyError):pass
    return None

def start(platform='blank'):
    current=probe();process=owned_process()
    if current and not process:
        raise ValueError('Configured port belongs to an unverified browser. Choose another port in runtime.json and rerun configure; do not attach.')
    if process and not current:
        raise ValueError('Dedicated Chrome is running but not responding. Inspect its window; do not launch another process or delete profile locks.')
    chrome=executable();PROFILE.mkdir(parents=True,exist_ok=True)
    if current:
        subprocess.run([chrome,f'--user-data-dir={PROFILE}',LOGIN[platform]],check=True,timeout=20,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        print('Opened the existing dedicated profile. Sign in yourself; the agent will not enter credentials.')
        return
    port=urlparse(browser_endpoint()).port
    # Refuse an active profile even if its original launcher state was lost.
    for proc in psutil.process_iter(['cmdline']):
        try:
            if f'--user-data-dir={PROFILE}' in (proc.info['cmdline'] or []):
                raise ValueError('This profile already has a running Chrome process. Close that dedicated browser normally before recovery.')
        except (psutil.AccessDenied,psutil.NoSuchProcess):continue
    (ROOT/'logs').mkdir(exist_ok=True);STATE.parent.mkdir(exist_ok=True)
    with (ROOT/'logs/browser.log').open('ab') as log:
        proc=subprocess.Popen([chrome,f'--user-data-dir={PROFILE}',
            '--remote-debugging-address=127.0.0.1',f'--remote-debugging-port={port}',
            '--no-first-run','--no-default-browser-check',LOGIN[platform]],
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            **({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}))
    STATE.write_text(json.dumps({'pid':proc.pid,'created':psutil.Process(proc.pid).create_time()}),encoding='utf-8')
    for _ in range(60):
        if probe():
            print('Dedicated Chrome is ready. Login is manual; this profile will be reused.');return
        if proc.poll() is not None:break
        time.sleep(.5)
    raise ValueError('Chrome did not become ready within 30 seconds. Inspect logs/browser.log; the process was not force-killed.')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['start','login','status'])
    p.add_argument('--platform',choices=LOGIN,default='linkedin')
    a=p.parse_args()
    try:
        if a.action=='status':
            result={'connected':bool(probe()),'owned':bool(owned_process()),'profile':str(PROFILE)}
            print(json.dumps(result));sys.exit(0 if result['connected'] and result['owned'] else 1)
        start(a.platform if a.action=='login' else 'blank')
    except (ValueError,subprocess.SubprocessError) as e:raise SystemExit(str(e)) from None

if __name__=='__main__':main()
