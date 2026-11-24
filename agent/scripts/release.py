"""Audit/export only public project files, excluding local state and Git history."""
import argparse
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_FILES = ['README.md','AGENTS.md','CLAUDE.md','LICENSE','CONTRIBUTING.md','SECURITY.md',
                '.gitignore','.gitattributes','requirements.txt','setup.py']
PUBLIC_DIRS = ['docs','templates','.github','agent']
SUFFIXES = {'.md','.py','.js','.json','.yaml','.yml','.toml'}

def public_files(root=ROOT):
    result=[root/name for name in PUBLIC_FILES]
    for name in PUBLIC_DIRS:
        result.extend(p for p in (root/name).rglob('*') if p.is_file()
                      and p.suffix in SUFFIXES and '__pycache__' not in p.parts)
    for p in result:
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
            raise ValueError('Refused symlink/outside path: '+str(p.relative_to(root)))
    return sorted(set(result))

def audit(root=ROOT):
    failures=[]
    patterns=[r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
              r'(?i)bearer\s+[A-Za-z0-9_-]{25,}',r'(?i)(?:password|client_secret)\s*[:=]\s*["\'][^"\']{12,}["\']']
    for p in public_files(root):
        text=p.read_text(encoding='utf-8')
        if any(re.search(pattern,text) for pattern in patterns):failures.append(str(p.relative_to(root)))
    if failures:raise ValueError('Potential credentials need review: '+', '.join(failures))
    return public_files(root)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--output',type=Path)
    a=p.parse_args()
    try:
        files=audit()
        if a.output:
            if a.output.exists():raise ValueError('Output exists; choose a new archive path.')
            with zipfile.ZipFile(a.output,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for f in files:z.write(f,'Shortlistr/'+f.relative_to(ROOT).as_posix())
            print('Public archive created:',a.output)
        print(f'Audited {len(files)} public files. Local profiles, configs, runtime data and Git history excluded.')
        print('This scan is a safeguard, not a guarantee; review the archive before publication.')
    except (OSError,ValueError) as e:raise SystemExit(str(e)) from None

if __name__=='__main__':main()
