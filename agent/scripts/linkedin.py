#!/usr/bin/env python3
"""Collect LinkedIn jobs; never score, apply, or send messages.

Use .venv/bin/python agent/scripts/linkedin.py --help.
Chrome must already be running with the authenticated profile and local CDP.
"""
from runtime import browser_endpoint
import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import file_lock as fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import anyio
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[2]
DATES = {"24h": "r86400", "week": "r604800", "month": "r2592000", "any": None}
DATE_LABELS = {"24h": "Past 24 hours", "week": "Past week", "month": "Past month"}
from runtime import geo_ids
GEO_IDS = geo_ids()

# The old notifier's scroll-container approach, with job IDs read from cards
# rather than clicking every card to recover its selected-pane URL.
CARDS_JS = r"""() => {
  const nodes = [...document.querySelectorAll('[componentkey^="job-card-component-ref-"]')];
  const jobs = new Map();
  for (const node of nodes) {
    const id = node.getAttribute('componentkey').match(/job-card-component-ref-(\d+)/)?.[1];
    if (!id || jobs.has(id)) continue;
    const card = node.matches('[role="button"]') ? node : node.querySelector('[role="button"]') || node;
    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
    const paragraphs = [...card.querySelectorAll('p')];
    const titleP = paragraphs[0];
    const hidden = titleP?.querySelector('[aria-hidden="true"]');
    const title = clean(hidden?.textContent || titleP?.innerText).replace(/\s*\(Verified job\)$/i, '');
    const repeated = title.match(/^(.+?)\s+\1$/);
    const titleCopy = repeated ? repeated[1] : title;
    const text = card.innerText || '';
    jobs.set(id, {platform:'linkedin', job_id:id,
      url:`https://www.linkedin.com/jobs/view/${id}/`, title:titleCopy,
      company:clean(paragraphs[1]?.innerText), location:clean(paragraphs[2]?.innerText),
      posted_text:(text.match(/Posted ([^\n]+)/i)||[])[1] || null,
      easy_apply:/Easy Apply/i.test(text), card_text:text});
  }
  return [...jobs.values()];
}"""

SCROLL_JS = r"""(advance) => {
  const card = document.querySelector('[componentkey^="job-card-component-ref-"]');
  let node = card?.parentElement;
  while (node && node !== document.body) {
    if (['auto','scroll','overlay'].includes(getComputedStyle(node).overflowY)
        && node.scrollHeight > node.clientHeight + 40) break;
    node = node.parentElement;
  }
  const container = node && node !== document.body ? node : document.scrollingElement;
  if (advance) container.scrollTop += Math.max(300, container.clientHeight * .8);
  return {top:container.scrollTop, height:container.scrollHeight,
    bottom:container.scrollTop + container.clientHeight >= container.scrollHeight - 8};
}"""


class ScrapeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Collection:
    def __init__(self, path):
        self.path = Path(path)
        self.jobs = {}
        if self.path.exists():
            # JSONL records use newline delimiters. Unicode line separators may
            # occur inside valid JSON strings and must not split a record.
            for line in self.path.read_text(encoding='utf-8').split('\n'):
                if line.strip():
                    job = json.loads(line)
                    self.jobs[str(job['job_id'])] = job

    def add(self, job):
        key = str(job['job_id'])
        if key in self.jobs:
            existing = self.jobs[key]
            # Older collections may contain description-only records.
            first_card = not existing.get('title') and bool(job.get('title'))
            for field, value in job.items():
                if not existing.get(field) and value is not None:
                    existing[field] = value
            return first_card
        self.jobs[key] = {**job, 'collected_at': utc_now()}
        return True

    def save(self):
        atomic_write(self.path, ''.join(json.dumps(j, ensure_ascii=False) + '\n'
                                      for j in self.jobs.values()))


@contextmanager
def scraper_lock():
    path = ROOT / 'data/linkedin.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ScrapeError('busy', 'Another scraper is running. Retry after it finishes.')
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def search_url(keyword, location, posted_within, remote_only=False):
    params = {'keywords': keyword, 'origin': 'JOBS_HOME_SEARCH_BUTTON'}
    params['geoId'] = GEO_IDS[location]
    if DATES[posted_within]:
        params['f_TPR'] = DATES[posted_within]
    if remote_only:
        params['f_WT'] = '2'
    path = 'search' if remote_only else 'search-results'
    return f'https://www.linkedin.com/jobs/{path}/?' + urlencode(params)


async def check_access(page):
    if any(x in page.url for x in ['/checkpoint', '/challenge', '/captcha']):
        raise ScrapeError('challenge', 'LinkedIn requires a security check. Stop and resolve manually.')
    if '/login' in page.url or '/authwall' in page.url:
        raise ScrapeError('authentication_required', 'Log in manually using the shared browser profile.')
    if await page.locator("input[name='session_key'], #username").count():
        raise ScrapeError('authentication_required', 'LinkedIn login is required.')
    for text in ['Verify your identity', "Let's do a quick security check", 'Security verification']:
        if await page.get_by_text(text, exact=True).is_visible():
            raise ScrapeError('challenge', 'LinkedIn requires a security check. Stop and resolve manually.')


async def goto(page, url):
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    for attempt in range(2):
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=45000)
            await check_access(page)
            return
        except PlaywrightTimeout:
            await check_access(page)
            if attempt:
                raise ScrapeError('navigation_failed', 'Page load timed out twice.')
            await asyncio.sleep(3)


async def results_ready(page):
    try:
        await page.wait_for_function("""() =>
          document.querySelector('[componentkey^="job-card-component-ref-"]') ||
          /(?:no results found|no matching jobs|0 results)/i.test(document.body.innerText)
        """, timeout=25000)
    except Exception:
        await check_access(page)
        raise ScrapeError('layout_changed', 'Neither result cards nor an explicit empty result appeared.')
    await check_access(page)


async def collect_page(page):
    jobs, stable, previous = {}, 0, None
    deadline = asyncio.get_running_loop().time() + 120
    while asyncio.get_running_loop().time() < deadline:
        await check_access(page)
        for job in await page.evaluate(CARDS_JS):
            jobs[job['job_id']] = job
        state = await page.evaluate(SCROLL_JS, False)
        signature = (len(jobs), state['top'], state['height'])
        stable = stable + 1 if signature == previous else 0
        if state['bottom'] and stable >= 2:
            return list(jobs.values()), True
        if stable >= 5:
            return list(jobs.values()), False
        previous = signature
        await page.evaluate(SCROLL_JS, True)
        await asyncio.sleep(.8)
    return list(jobs.values()), False


def checkpoint(args, collection, selected, summary):
    # Persist explicit completeness even if collection stops before description visits.
    for key in selected:
        job = collection.jobs[key]
        job.setdefault('external_apply_url', None)
        job['description_status'] = 'complete' if job.get('description') else 'incomplete'
    atomic_write(args.output, json.dumps({'summary': summary,
        'jobs': [collection.jobs[k] for k in selected]}, ensure_ascii=False, indent=2) + '\n')
    collection.save()


async def verify_search_scope(page, location, remote_only):
    """Require the selected country and workplace scope, not just URL parameters."""
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    try:
        await page.get_by_role('button', name=f'Location {location}', exact=True).wait_for(timeout=10000)
        workplace = 'Remote' if remote_only else 'Workplace type'
        await page.get_by_role('checkbox', name=workplace, exact=True,
                               checked=remote_only).wait_for(timeout=10000)
    except PlaywrightTimeout:
        await check_access(page)
        scope = 'Remote' if remote_only else 'all workplace types'
        raise ScrapeError('filter_not_applied', f'LinkedIn did not confirm {location} and {scope}.')


async def search(page, args, collection, selected, summary):
    from assessment_policy import recheck_ids
    reconsider = recheck_ids("linkedin")
    await goto(page, search_url(args.keyword, args.location, args.posted_within, args.remote_only))
    await results_ready(page)
    await verify_search_scope(page, args.location, args.remote_only)
    if args.posted_within != 'any':
        active = page.get_by_role('checkbox', name=DATE_LABELS[args.posted_within], exact=True)
        if not await active.count() or not await active.is_checked():
            raise ScrapeError('filter_not_applied', 'LinkedIn did not confirm the requested date filter.')
    page_number, signatures, observed = 1, set(), set()
    while True:
        jobs, complete = await collect_page(page)
        signature = tuple(sorted(j['job_id'] for j in jobs))
        if signature and signature in signatures:
            raise ScrapeError('pagination_stalled', 'Pagination repeated the same set of job IDs.')
        signatures.add(signature)
        summary['pages_scanned'] += 1
        for job in jobs:
            key = job['job_id']
            if key in observed:
                continue
            observed.add(key)
            if not job['title'] or not job['company']:
                raise ScrapeError('layout_changed', 'A job card is missing title or company.')
            if collection.add(job):
                selected.append(key)
                summary['new_jobs'] = summary.get('new_jobs', 0) + 1
            elif not collection.jobs[key].get('description'):
                selected.append(key)
                summary['retried_jobs'] = summary.get('retried_jobs', 0) + 1
            else:
                summary['already_collected'] += 1
                if key in reconsider:
                    selected.append(key)
                    summary['reconsidered_jobs'] = summary.get('reconsidered_jobs', 0) + 1
        summary['status'] = 'in_progress'
        checkpoint(args, collection, selected, summary)
        if not complete:
            raise ScrapeError('scroll_stalled', 'Could not confirm the end of the results list.')
        next_button = page.get_by_role('button', name=f'Page {page_number + 1}', exact=True)
        if not await next_button.count() or not await next_button.is_enabled():
            summary['status'] = 'complete'
            return
        if args.max_pages and page_number >= args.max_pages:
            summary.update(status='partial', reason='Requested page limit reached.')
            return
        await asyncio.sleep(3)
        await next_button.click(timeout=10000)
        # Hydration may leave the previous page visible briefly after clicking.
        try:
            await page.wait_for_function("""(old) => {
              const ids = [...new Set([...document.querySelectorAll('[componentkey^="job-card-component-ref-"]')]
                .map(n => n.getAttribute('componentkey').replace('job-card-component-ref-', '')))];
              return ids.length && ids.some(id => !old.includes(id));
            }""", arg=list(signature), timeout=25000)
        except Exception:
            await check_access(page)
            raise ScrapeError('pagination_stalled', 'The next page did not produce a new set of cards.')
        await results_ready(page)
        page_number += 1


def external_destination(href):
    """Decode the observed LinkedIn safety wrapper without making a request."""
    if not href:
        return None
    try:
        parsed = urlparse(href)
        if parsed.hostname in ['www.linkedin.com', 'linkedin.com'] and parsed.path == '/safety/go/':
            href = parse_qs(parsed.query).get('url', [None])[0]
            if not href:
                return None
            parsed = urlparse(href)
        host = parsed.hostname or ''
        if (parsed.scheme not in ['http', 'https'] or not host
                or host == 'linkedin.com' or host.endswith('.linkedin.com')
                or parsed.username or parsed.password):
            return None
        return href
    except ValueError:
        return None


async def extract_external_apply_url(page):
    # Read only explicit application links, never arbitrary links in the JD.
    links = page.get_by_role('main').get_by_role(
        'link', name=re.compile(r'^Apply(?: on company website)?$', re.I))
    urls = set()
    for link in await links.all():
        if await link.is_visible():
            url = external_destination(await link.get_attribute('href'))
            if url:
                urls.add(url)
    return urls.pop() if len(urls) == 1 else None


async def fetch_description(page, key):
    await goto(page, f'https://www.linkedin.com/jobs/view/{key}/')
    try:
        await page.get_by_role('heading', name='About the job', exact=True).wait_for(timeout=25000)
    except Exception:
        await check_access(page)
        raise ScrapeError('description_unavailable', f'Job {key}: About the job did not appear.')
    # Classic detail pages use .jobs-description; the new UI uses a section
    # enclosing the About the job heading. Never use the entire page text.
    section = page.locator('.jobs-description__content')
    if not await section.count():
        section = page.get_by_role('heading', name='About the job', exact=True).locator('..')
        # The current UI wraps the heading separately from the description.
        for _ in range(3):
            if len((await section.inner_text()).strip()) >= 120:
                break
            section = section.locator('..')
            if await section.get_by_role('heading').count() != 1:
                raise ScrapeError('layout_changed', f'Job {key}: no isolated description region.')
    if await section.count() != 1:
        raise ScrapeError('layout_changed', f'Job {key}: description region is ambiguous.')
    more = section.get_by_role('button', name=re.compile(r'(show more|see more)', re.I))
    if await more.count() == 1 and await more.is_visible():
        await more.click(timeout=5000)
    description = (await section.inner_text()).strip()
    if len(description) < 120:
        raise ScrapeError('description_unavailable', f'Job {key}: description is missing or too short.')
    return description


async def enrich_results(page, args, collection, selected, summary):
    collection_status = summary['status']
    summary['status'] = 'in_progress'
    for index, key in enumerate(selected):
        job = collection.jobs[key]
        if job.get('description'):
            continue
        try:
            job['description'] = await fetch_description(page, key)
            job['external_apply_url'] = await extract_external_apply_url(page)
            job['description_fetched_at'] = utc_now()
            job.pop('description_error', None)
            summary['fetched_descriptions'] += 1
        except Exception as exc:
            code = getattr(exc, 'status', 'browser_error')
            job['description_error'] = {'code': code, 'reason': str(exc).split('\n')[0]}
            if code in ['authentication_required', 'challenge']:
                raise
        finally:
            checkpoint(args, collection, selected, summary)
        if index < len(selected) - 1:
            await asyncio.sleep(3)
    missing = sum(not collection.jobs[key].get('description') for key in selected)
    summary['status'] = collection_status
    summary['incomplete_descriptions'] = missing
    if missing:
        summary.update(status='partial')
        summary['reason'] = (summary.get('reason', '') + ' Some job descriptions could not be retrieved.').strip()


@asynccontextmanager
async def playwright_client():
    """Disconnect even when the MCP request's cancellation scope is active."""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        yield pw
    finally:
        with anyio.move_on_after(5, shield=True):
            await pw.stop()


async def run(args, emit=True):
    collection, selected = Collection(args.cache), []
    summary = {'status':'in_progress', 'operation':args.command,
               'results_file':str(args.output), 'collection_file':str(args.cache)}
    summary.update(keyword=args.keyword, location=args.location, remote_only=args.remote_only,
                   posted_within=args.posted_within, pages_scanned=0,
                   new_jobs=0, retried_jobs=0, already_collected=0,
                   fetched_descriptions=0, incomplete_descriptions=0)
    page = None
    async with playwright_client() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(args.cdp_endpoint, timeout=15000)
            if not browser.contexts:
                raise ScrapeError('browser_unavailable', 'Shared browser has no persistent context.')
            page = await browser.contexts[0].new_page()
            page.set_default_timeout(10000)
            await search(page, args, collection, selected, summary)
            await enrich_results(page, args, collection, selected, summary)
        except asyncio.CancelledError:
            summary.update(status='interrupted', error='cancelled',
                           reason='Collection cancelled. Register this saved result before starting another search.')
            raise
        except Exception as exc:
            summary.update(status='partial' if selected else 'error',
                           error=getattr(exc, 'status', 'browser_error'), reason=str(exc).split('\n')[0])
        finally:
            summary['incomplete_descriptions'] = sum(
                not collection.jobs[key].get('description') for key in selected)
            if page and summary['status'] not in ['complete']:
                summary['inspection_url'] = page.url
            checkpoint(args, collection, selected, summary)
            if page:
                if summary['status'] == 'interrupted':
                    # Only our collection tab: stop its pending navigation as well.
                    # Shield cleanup from MCP's level-triggered cancellation.
                    with anyio.move_on_after(5, shield=True):
                        try:
                            await page.close()
                        except Exception:
                            pass
                # Leave a blocked page available for manual inspection/login.
                elif summary['status'] == 'complete' or summary.get('reason') == 'Requested page limit reached.':
                    await page.close()
                else:
                    summary['inspection_url'] = page.url
            # No browser.close(): Chrome belongs to the shared browser service.
    if emit:
        print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['search']:
        p = sub.add_parser(name)
        p.add_argument('--cdp-endpoint', default=browser_endpoint())
        p.add_argument('--cache', type=Path, default=ROOT / 'data/linkedin-jobs.jsonl')
        p.add_argument('--output', type=Path, help='JSON file containing this call\'s results')
    p = sub.choices['search']
    p.add_argument('--keyword', required=True)
    p.add_argument('--location', required=True, choices=sorted(GEO_IDS))
    p.add_argument('--posted-within', choices=DATES, default='week')
    p.add_argument('--remote-only', action='store_true')
    p.add_argument('--max-pages', type=int, help='Optional explicit limit; default is all pages')
    args = parser.parse_args(argv)
    if urlparse(args.cdp_endpoint).hostname not in ['127.0.0.1', 'localhost', '::1']:
        parser.error('The shared browser endpoint must be local.')
    if args.command == 'search' and args.max_pages is not None and args.max_pages < 1:
        parser.error('--max-pages must be positive')
    args.output = args.output or ROOT / 'runs' / datetime.now().strftime('%Y-%m-%d_%H%M%S_%f') / f'linkedin-{args.command}.json'
    if args.output.resolve() == args.cache.resolve():
        parser.error('--output must differ from --cache')
    return args


def main():
    args = parse_args()
    os.umask(0o077)
    try:
        with scraper_lock():
            summary = asyncio.run(run(args))
            return 0 if summary['status'] == 'complete' else 2
    except Exception as exc:
        print(json.dumps({'status':'error', 'error':getattr(exc,'status',type(exc).__name__), 'reason':str(exc)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
