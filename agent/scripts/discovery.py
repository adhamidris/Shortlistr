"""Public API/RSS collectors. Persistent response cache is separate from decisions."""
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import httpx
from job_identity import greenhouse_ref
from linkedin import ROOT, atomic_write

DATA = ROOT / 'data/discovery'
WWR_FEEDS = {'all': 'remote-jobs', 'programming': 'categories/remote-programming-jobs',
             'other': 'categories/all-other-remote-jobs',
             'sales-marketing': 'categories/remote-sales-and-marketing-jobs'}


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.link = None
    def handle_data(self, data):
        self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            self.link = href if href.startswith(('https://','http://','mailto:')) else None
        if tag in {'p','br','li','div','h2','h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'a' and self.link:
            self.parts.append(' (' + self.link + ')')
            self.link = None


def plain(value):
    parser = Text(); parser.feed(unescape(value or ''))
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())


def api_root(region):
    return 'https://boards-api' + ('.eu' if region == 'eu' else '') + '.greenhouse.io/v1/boards/'


class Fetcher:
    def __init__(self, directory=DATA, client=None):
        self.directory = Path(directory)
        self.client = client
        self.last_request = 0
        self.sources = []

    async def get(self, url, ttl):
        path = self.directory / 'responses' / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        cached = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if cached and time.time() - cached['fetched_at'] < ttl:
            self.sources.append({'url':url, 'fetched_at':cached['fetched_at'], 'cached':True})
            return cached['body']
        # Repeated tool calls respect source backoff too, not just this process.
        pause = self.directory / 'retry-after.json'
        backoff = json.loads(pause.read_text(encoding='utf-8')) if pause.exists() else {}
        host = httpx.URL(url).host
        if backoff.get(host, 0) > time.time():
            raise ValueError(f'{host} requested backoff until {datetime.fromtimestamp(backoff[host], timezone.utc).isoformat()}')
        await asyncio.sleep(max(0, 3 - (time.monotonic() - self.last_request)))
        for attempt in range(2):
            self.last_request = time.monotonic()
            response = await self.client.get(url, headers={'If-None-Match':cached['etag']} if cached and cached.get('etag') else {})
            if response.status_code == 429:
                raw = response.headers.get('retry-after', '60')
                try:
                    delay = max(60, float(raw))
                except ValueError:
                    try: delay = max(60, parsedate_to_datetime(raw).timestamp() - time.time())
                    except (ValueError, TypeError): delay = 60
                backoff[host] = time.time() + delay
                atomic_write(pause, json.dumps(backoff))
                raise ValueError(f'{host} rate limited; retry after {int(delay)} seconds. No automatic retry.')
            if response.status_code >= 500 and attempt == 0:
                await asyncio.sleep(5)
                continue
            if response.status_code == 304 and cached:
                body = cached['body']
            else:
                response.raise_for_status()
                body = response.text
            record = {'body':body, 'fetched_at':time.time(), 'etag':response.headers.get('etag') or (cached or {}).get('etag')}
            atomic_write(path, json.dumps(record))
            self.sources.append({'url':url,'fetched_at':record['fetched_at'],'cached':False})
            return body
        raise ValueError('Source unavailable')


def remotive(body):
    payload = json.loads(body)
    rows = payload['jobs']
    if payload.get('job-count', len(rows)) != len(rows):
        raise ValueError('Remotive job count did not match response.')
    return [dict(platform='remotive', job_id=str(j['id']), title=j['title'], company=j['company_name'],
                 url=j['url'], location=j.get('candidate_required_location',''), description=plain(j['description']),
                 posted_at=j.get('publication_date'), employment_type=j.get('job_type'), salary=j.get('salary'),
                 application_route='See application instructions in description or source listing') for j in rows]


def wwr(body):
    root = ET.fromstring(body)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('Expected RSS, not a challenge or login page.')
    jobs = []
    for row in root.findall('./channel/item'):
        title = row.findtext('title', '')
        company, sep, role = title.partition(': ')
        if not sep:
            raise ValueError('WWR title missing company separator.')
        url = row.findtext('link')
        date = row.findtext('pubDate')
        jobs.append(dict(platform='weworkremotely',job_id=row.findtext('guid') or url,url=url,
                         company=company,title=role,description=plain(row.findtext('description')),
                         location=row.findtext('region',''),posted_at=parsedate_to_datetime(date).isoformat() if date else None,
                         application_route='Source listing; employer destination not yet verified'))
    return jobs


def greenhouse(body, region, board):
    payload = json.loads(body)
    rows = payload['jobs']
    if payload.get('meta',{}).get('total',len(rows)) != len(rows):
        raise ValueError('Greenhouse job count did not match response.')
    host = 'job-boards.eu.greenhouse.io' if region == 'eu' else 'job-boards.greenhouse.io'
    return [dict(platform='greenhouse',job_id=f'{region}:{j["id"]}',title=j['title'],
                 company=j.get('company_name') or board,url=j['absolute_url'],
                 external_apply_url=f'https://{host}/{board}/jobs/{j["id"]}',
                 location=j.get('location',{}).get('name',''),description=plain(j.get('content')),
                 posted_at=j.get('first_published'),updated_at=j.get('updated_at'),
                 application_route='greenhouse') for j in rows]


def normalized(value):
    return ' '.join(re.findall(r'\w+', value.casefold()))


def parse_date(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def collect(source, keyword, output, since=None, board_url=None, feeds=None, directory=DATA, client=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Output exists. Register it; use a fresh path for another query.')
    cutoff = parse_date(since) if since else datetime.now(timezone.utc) - timedelta(days=7)
    summary = dict(platform=source, keyword=keyword or '(all)', location='Remote eligibility assessed from description',
                   posted_within=cutoff.isoformat(), status='in_progress',results_file=str(output), matched_jobs=0,filter_counts={'examined':0, 'outside_date_window':0, 'keyword_mismatch':0},coverage='Available public feed snapshot; not a complete historical search')
    jobs = {}; fetch = Fetcher(directory,client)
    def save():
        summary['sources'] = fetch.sources
        summary['matched_jobs'] = len(jobs)
        atomic_write(output,json.dumps({'summary':summary,'jobs':list(jobs.values())},ensure_ascii=False,indent=2))
    try:
        if source == 'remotive':
            requests = [('https://remotive.com/api/remote-jobs',21600,remotive)]
        elif source == 'weworkremotely':
            selected = feeds or ['all','programming','other','sales-marketing']
            if not selected or any(f not in WWR_FEEDS for f in selected):
                raise ValueError('Unknown WWR feed. Use all, programming, other, sales-marketing.')
            requests = [('https://weworkremotely.com/'+WWR_FEEDS[f]+'.rss',900,wwr) for f in dict.fromkeys(selected)]
        elif source == 'greenhouse':
            region, board, _ = greenhouse_ref(board_url or '')
            summary['coverage'] = 'All published jobs returned by the selected employer board'
            summary['board_url'] = board_url
            requests = [(api_root(region)+board+'/jobs?content=true',900,lambda body:greenhouse(body,region,board))]
        else:
            raise ValueError('Unknown source.')
        for url, ttl, parser in requests:
            for job in parser(await fetch.get(url,ttl)):
                summary['filter_counts']['examined'] += 1
                posted = parse_date(job.get('posted_at'))
                if posted and posted < cutoff:
                    summary['filter_counts']['outside_date_window'] += 1
                    continue
                if keyword and (' '+normalized(keyword)+' ') not in (' '+normalized(job['title']+' '+job['description'])+' '):
                    summary['filter_counts']['keyword_mismatch'] += 1
                    continue
                job['description_complete'] = bool(job['description'])
                # Return cached matching jobs too: collecting is not a decision. The ledger
                # resolves old decisions and run registration merges repeated query IDs.
                jobs[job['job_id']] = job
            save()
        summary['status'] = 'complete'
    except asyncio.CancelledError:
        summary.update(status='interrupted',reason='Cancelled; register saved partial results before retrying.')
        raise
    except Exception as exc:
        summary.update(status='partial' if jobs else 'error',reason=str(exc))
    finally:
        save()
    return summary
