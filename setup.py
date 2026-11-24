#!/usr/bin/env python3
"""Bootstrap the local environment. Run with Python 3.11+; no admin needed."""
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent

def main():
    if sys.version_info < (3, 11):
        raise SystemExit('Install Python 3.11 or newer from python.org, then retry.')
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT/'requirements.txt')], check=True)
    subprocess.run([str(python), str(ROOT/'agent/scripts/setup_project.py'), *sys.argv[1:]], cwd=ROOT, check=True)

if __name__ == '__main__':
    main()
