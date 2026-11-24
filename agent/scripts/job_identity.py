"""Conservative cross-source identity; titles alone are not cross-source proof."""
import re
from urllib.parse import urlsplit, parse_qs


def greenhouse_ref(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in {'boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'}:
        raise ValueError('Use a public Greenhouse board or job URL.')
    if p.path in {'/embed/job_app','/embed/job_board'}:
        query = parse_qs(p.query)
        board = query.get('for', [''])[0]
        job = query.get('token', [''])[0] if p.path.endswith('job_app') else None
        if not re.fullmatch(r'[A-Za-z0-9_-]+', board) or (job is not None and not job.isdigit()):
            raise ValueError('Invalid embedded Greenhouse board/job URL.')
        return ('eu' if '.eu.' in p.hostname else 'us'), board, job
    parts = p.path.strip('/').split('/')
    if not parts or not re.fullmatch(r'[A-Za-z0-9_-]+', parts[0]):
        raise ValueError('Missing Greenhouse board token.')
    job = parts[2] if len(parts) == 3 and parts[1] == 'jobs' and parts[2].isdigit() else None
    if len(parts) != 1 and job is None:
        raise ValueError('Use /BOARD or /BOARD/jobs/ID.')
    region = 'eu' if '.eu.' in p.hostname else 'us'
    return region, parts[0], job


def application_identity(url):
    try:
        region, board, job = greenhouse_ref(url)
        if job:
            return f'greenhouse:{region}:{job}'
    except ValueError:
        pass
    try:
        p = urlsplit(url or '')
    except ValueError:
        return None
    # gh_jid is Greenhouse's public job-post identifier on custom career pages.
    gh = parse_qs(p.query).get('gh_jid', [])
    if p.scheme == 'https' and len(gh) == 1 and gh[0].isdigit():
        return f'greenhouse:us:{gh[0]}'
    known = {
        'jobs.lever.co': r'/([^/]+)/([0-9a-f-]{36})(?:/apply)?/?',
        'jobs.eu.lever.co': r'/([^/]+)/([0-9a-f-]{36})(?:/apply)?/?',
        'jobs.ashbyhq.com': r'/([^/]+)/([0-9a-f-]{36})(?:/application)?/?',
        'apply.workable.com': r'/([^/]+)/j/([A-Za-z0-9]+)(?:/apply)?/?',
    }
    if p.scheme == 'https' and p.hostname in known:
        match = re.fullmatch(known[p.hostname], p.path)
        if match:
            return p.hostname + ':' + ':'.join(match.groups())
    # Do not equate generic careers/signup URLs or strip meaningful query arguments.
    if p.scheme in {'https', 'http'} and '/jobs/view/' in p.path and p.hostname in {'linkedin.com','www.linkedin.com'}:
        m = re.fullmatch(r'/jobs/view/(\d+)/?', p.path)
        if m:
            return 'linkedin:' + m[1]
    return None


def identities(job):
    return {v for field in ('url', 'external_apply_url')
            if (v := application_identity(job.get(field, '')))}


def same_posting(a, b):
    return bool(identities(a) & identities(b))
