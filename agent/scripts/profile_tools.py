"""Import a CV and validate onboarding without inventing candidate information."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import yaml
from runtime import ROOT, geo_ids

FILES = ['profile/identity.yaml','profile/qa-bank.md','config/preferences.yaml','config/targets.yaml']

def read(path):return yaml.safe_load((ROOT/path).read_text(encoding='utf-8'))

def import_cv(source):
    source=Path(source).expanduser().resolve()
    if source.suffix.lower() not in ('.pdf','.docx','.txt','.md'):
        raise ValueError('Provide PDF, DOCX, TXT or Markdown. Export old DOC files to PDF first.')
    dest=ROOT/'profile/cv'/('resume'+source.suffix.lower());dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists() and dest.read_bytes()!=source.read_bytes():
        raise ValueError('A different resume already exists. Discuss replacement; do not overwrite silently.')
    if source!=dest:shutil.copy2(source,dest)
    if dest.suffix=='.pdf':
        from pypdf import PdfReader
        text='\n\n'.join(page.extract_text() or '' for page in PdfReader(dest).pages)
    elif dest.suffix=='.docx':
        with ZipFile(dest) as z:
            tree=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text='\n'.join(''.join(n.itertext()) for n in tree.findall('.//w:t',ns))
    else:text=dest.read_text(encoding='utf-8')
    (ROOT/'profile/cv/cv-extracted.md').write_text(text,encoding='utf-8')
    print(json.dumps({'cv_path':str(dest.relative_to(ROOT)),'extracted_characters':len(text),
                      'needs_visual_review':len(text.strip())<100}))

def issues():
    errors=[]
    try:
        identity=read(FILES[0]);prefs=read(FILES[2]);targets=read(FILES[3])
        for field in ['name','email','location','timezone','cv_path']:
            if not identity.get(field):errors.append('Missing identity.'+field)
        if identity.get('timezone'):
            try:ZoneInfo(identity['timezone'])
            except Exception:errors.append('Invalid IANA timezone')
        cv=(ROOT/(identity.get('cv_path') or 'missing')).resolve()
        if not cv.is_relative_to((ROOT/'profile/cv').resolve()) or not cv.is_file():errors.append('CV must exist under profile/cv/')
        if not prefs.get('roles',{}).get('primary'):errors.append('No confirmed target roles')
        threshold=prefs.get('scoring',{}).get('auto_apply_threshold')
        if isinstance(threshold,bool) or not isinstance(threshold,(float,int)) or not 0<=threshold<=100:errors.append('Choose a score threshold from 0 to 100')
        if prefs.get('application',{}).get('mode')!='automatic':errors.append('This edition expects automatic applications on a manually triggered run')
        if targets.get('run',{}).get('trigger')!='manual':errors.append('Manual triggering is the supported default')
        enabled=[p for p in targets.get('platforms',[]) if p.get('enabled')]
        if not enabled:errors.append('No search platform enabled')
        for p in enabled:
            if p['name']=='linkedin':
                if not p.get('search_queries'):errors.append('LinkedIn search keywords missing')
                for location in [p.get('location'),*p.get('remote_locations',[]),*p.get('relocation_locations',[])]:
                    if location not in geo_ids():errors.append('Unverified LinkedIn location: '+str(location))
    except (OSError,ValueError,TypeError,KeyError) as e:errors.append('Profile configuration incomplete: '+str(e))
    return errors

def fingerprint():
    paths=list(FILES)+['config/runtime.json']
    identity=read(FILES[0]);paths += [identity['cv_path'],'profile/cv/cv-extracted.md']
    digest=hashlib.sha256()
    for name in paths:
        digest.update(name.encode());digest.update((ROOT/name).read_bytes())
    return digest.hexdigest()

def validate(confirm=False):
    errors=issues()
    if errors:
        print(json.dumps({'ready':False,'missing':errors},indent=2));return False
    digest=fingerprint();path=ROOT/'data/onboarding.json'
    if confirm:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'confirmed_at':datetime.now(timezone.utc).isoformat(),'profile_hash':digest}),encoding='utf-8')
    state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    ready=state.get('profile_hash')==digest
    print(json.dumps({'ready':ready,'profile_valid':True,'criteria_confirmation_required':not ready}))
    return ready

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('import-cv').add_argument('path')
    sub.add_parser('validate').add_argument('--confirm',action='store_true',help='Use only after the user confirms the concise criteria summary.')
    a=p.parse_args()
    try:
        if a.action=='import-cv':import_cv(a.path)
        elif not validate(a.confirm):sys.exit(1)
    except (OSError,ValueError) as e:raise SystemExit(str(e)) from None

if __name__=='__main__':main()
