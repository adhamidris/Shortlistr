"""Machine-specific settings; no candidate policy belongs in runtime code."""
import json
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]

def settings():
    path = ROOT / 'config/runtime.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

def browser_endpoint():
    value = settings().get('browser_endpoint', 'http://127.0.0.1:9224')
    parsed = urlparse(value)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path not in ('', '/') or parsed.username:
        raise ValueError('Browser endpoint must be http://127.0.0.1:<port>.')
    return value.rstrip('/')

def local_zone():
    return ZoneInfo(settings().get('timezone', 'UTC'))

def geo_ids():
    # These identifiers are observed LinkedIn values, not inferred geography.
    builtins = {'Egypt':'106155005', 'Saudi Arabia':'100459316',
                'United Arab Emirates':'104305776', 'Germany':'101282230',
                'Netherlands':'102890719', 'Ireland':'104738515'}
    extra = settings().get('linkedin_geo_ids', {})
    if any(not str(v).isdigit() for v in extra.values()):
        raise ValueError('LinkedIn geography IDs must be numeric verified IDs.')
    return builtins | {k:str(v) for k,v in extra.items()}
