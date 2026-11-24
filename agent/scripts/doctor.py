"""Read-only readiness checks; never submits, logs in or mutates browser state."""
import importlib.metadata
import json
import shutil
import sys
from runtime import ROOT, local_zone, browser_endpoint
from browser import executable, probe, owned_process
from profile_tools import issues

def main():
    checks=[]
    def check(name, fn):
        try:checks.append({'check':name,'ok':True,'detail':fn()})
        except Exception as e:checks.append({'check':name,'ok':False,'detail':str(e)})
    check('python',lambda:sys.version.split()[0])
    for package in ['playwright','mcp','portalocker','PyYAML','tzdata']:
        check(package,lambda p=package:importlib.metadata.version(p))
    check('chrome',executable)
    check('timezone',lambda:str(local_zone()))
    check('browser_endpoint',browser_endpoint)
    check('node',lambda:shutil.which('node') or (_ for _ in ()).throw(ValueError('Install Node.js LTS')))
    checks.append({'check':'dedicated_browser','ok':bool(probe()) and bool(owned_process()),
                   'detail':'Use browser.py start/login if absent; do not attach to an unrelated profile.'})
    missing=issues();checks.append({'check':'profile','ok':not missing,'detail':missing})
    print(json.dumps(checks,indent=2))
    print('Tool discovery and live client availability require separate checks; see docs/onboarding.md.')
    return 0 if all(x['ok'] for x in checks) else 1

if __name__=='__main__':sys.exit(main())
