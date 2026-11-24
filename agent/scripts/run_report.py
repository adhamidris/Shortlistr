#!/usr/bin/env python3
"""Register collected jobs and record compact outcomes; generate reports without sending.

collect --run runs/NAME --jobs RESULTS.json [--jobs MORE.json]
record --run runs/NAME --decisions decisions.json (or - for stdin)
blocker --run runs/NAME --id NAME --reason TEXT (or --resolve)
preview --run runs/NAME
finalize --run runs/NAME
"""
from runtime import local_zone
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import html
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

import ledger
from assessment_policy import VERSION as ASSESSMENT_POLICY
from job_identity import same_posting

ROOT = Path(__file__).resolve().parents[2]
FINAL = {'applied', 'skipped', 'needs_input'}
STATUSES = FINAL | {'applying'}
ATTENTION_LOOKBACK_RUNS = 2
FIELDS = ('platform', 'job_id', 'title', 'company', 'location', 'url', 'external_apply_url')


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def run_lock(run):
    import file_lock as fcntl
    run = Path(run)
    run.mkdir(parents=True, exist_ok=True)
    with (run / '.report.lock').open('a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def key(job):
    return f"{job.get('platform', 'linkedin')}:{job['job_id']}"


def run_id(run):
    path = Path(run).resolve()
    try:
        return str(path.relative_to(ROOT / 'runs'))
    except ValueError:
        return str(path)


def manifest(run):
    path = Path(run) / 'report-input.json'
    if not path.exists():
        raise ValueError('Register collection results first with collect --jobs.')
    return json.loads(path.read_text(encoding='utf-8'))


def event_for(job, run, status, **extra):
    e = {field: job[field] for field in FIELDS if job.get(field) is not None}
    e.update(event=status, run=run_id(run), **extra)
    if status in STATUSES:
        e['assessment_policy'] = ASSESSMENT_POLICY
    e['job_hash'] = ledger.job_hash(e['platform'], e['job_id'], e.get('company'), e.get('title'))
    e['event_id'] = hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()
    e['ts'] = now()
    return e


def matches(event, job):
    if same_posting(event, job):
        return True
    if event.get('platform') != job['platform']:
        return False
    if str(event.get('job_id')) == job['job_id']:
        return True
    return bool(job.get('company') and job.get('title') and
                ledger.normalize(event.get('company')) == ledger.normalize(job['company']) and
                ledger.normalize(event.get('title')) == ledger.normalize(job['title']))


def prior_outcome(job, events, current_run, assessment_policy=None):
    relevant = [e for e in events if e.get('run') != current_run and matches(e, job)]
    if any(e.get('event') in {'applied', 'interview', 'offer', 'rejected'} for e in relevant):
        return {'status': 'previously_resolved', 'reason': 'previous application'}
    decisions = [e for e in relevant if e.get('event') in STATUSES]
    if not decisions:
        return {'status': 'unresolved', 'reason': None}
    last = decisions[-1]
    if last['event'] == 'skipped':
        if assessment_policy == ASSESSMENT_POLICY and last.get('assessment_policy') != ASSESSMENT_POLICY:
            return {'status': 'unresolved', 'reason': 'Reassess under current software-fit policy',
                    'prior_reason': last.get('notes'), 'prior_run': last.get('run')}
        return {'status': 'previously_resolved', 'reason': 'previous skip'}
    # Seen/scored events do not clear a draft or blocker. Keep only actionable
    # context here; the original ledger entry remains available when needed.
    reason = ' '.join(str(last.get('notes') or 'Inspect the existing application before retrying.').split())
    if len(reason) > 400:
        reason = reason[:397] + '…'
    return {'status': last['event'], 'reason': reason,
            'prior_run': last.get('run'), 'score': last.get('score')}


def new_manifest(run):
    return {'created_at': now(), 'run': run_id(run), 'assessment_policy': ASSESSMENT_POLICY, 'jobs': {}, 'sources': {}}


def blocker(run, blocker_id, reason=None, resolve=False):
    if not blocker_id.strip() or (not resolve and (not reason or not reason.strip())):
        raise ValueError('Provide a blocker ID and a short reason (or --resolve).')
    path = Path(run) / 'report-input.json'
    data = manifest(run) if path.exists() else new_manifest(run)
    entries = data.setdefault('blockers', {})
    if resolve and blocker_id not in entries and not any(
            b['id'] == blocker_id for b in run_blockers(data, ledger.read_events())):
        raise ValueError('Unknown blocker ID.')
    entries[blocker_id] = {'reason': reason, 'resolved': resolve, 'updated_at': now()}
    atomic_json(path, data)
    return render(run)


def run_blockers(data, events):
    entries = {}
    for event in events:
        if (event.get('run') == data['run'] and not event.get('job_id') and
                event.get('event') in {'needs_input', 'platform_blocked'}):
            bid = 'legacy-' + hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()[:16]
            entries[bid] = {'reason': event.get('notes') or 'Run needs attention.', 'resolved': False}
    entries.update(data.get('blockers', {}))
    return [{'id':bid, 'reason':entry['reason']} for bid, entry in entries.items() if not entry['resolved']]


def collect(run, paths):
    run = Path(run)
    data = manifest(run) if (run / 'report-input.json').exists() else new_manifest(run)
    for path in paths:
        path = Path(path).resolve()
        payload = json.loads(path.read_text(encoding='utf-8'))
        jobs = payload['jobs'] if isinstance(payload, dict) else payload
        if not isinstance(jobs, list):
            raise ValueError('Collection must be a list or an object with a jobs list.')
        summary = payload.get('summary', {}) if isinstance(payload, dict) else {}
        summary = dict(summary)
        summary.setdefault('platform', jobs[0].get('platform', 'linkedin') if jobs else
                           ('linkedin' if summary.get('operation') == 'search' else 'unspecified'))
        data['sources'][str(path)] = summary
        for item in jobs:
            job = {field: item[field] for field in FIELDS if item.get(field) is not None}
            job.setdefault('platform', 'linkedin')
            if not all(job.get(field) for field in ('job_id', 'title', 'company', 'url')):
                raise ValueError('Collected jobs need job_id, title, company, and url.')
            job['job_id'] = str(job['job_id'])
            equivalent = next((k for k, old in data['jobs'].items()
                               if k != key(job) and same_posting(old, job)), None)
            if equivalent is not None:
                # Keep the first registered ID stable so existing decisions still attach.
                old = data['jobs'][equivalent]
                if job.get('external_apply_url') and not old.get('external_apply_url'):
                    old['external_apply_url'] = job['external_apply_url']
            else:
                data['jobs'][key(job)] = job
    atomic_json(run / 'report-input.json', data)
    # Register before adding seen events: a retry can reconstruct either step.
    ledger.append_events([event_for(job, run, 'seen') for job in data['jobs'].values()])
    return render(run)


def expand_decisions(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict) or set(payload) - STATUSES:
        raise ValueError('Use a list of decisions or applied/skipped/needs_input/applying groups.')
    result = []
    for status, items in payload.items():
        if not isinstance(items, list):
            raise ValueError('Each outcome group must be a list.')
        for item in items:
            row = dict(item) if isinstance(item, dict) else {'job_id': str(item)}
            if 'status' in row and row['status'] != status:
                raise ValueError('Conflicting outcome group and status.')
            row['status'] = status
            result.append(row)
    return result


def record(run, payload):
    data = manifest(run)
    events = ledger.read_events()
    existing_ids = {e.get('event_id') for e in events}
    pending, seen = [], set()
    for decision in expand_decisions(payload):
        if not isinstance(decision, dict) or set(decision) - {'platform','job_id','status','score','reason','evidence'}:
            raise ValueError('Decision fields: job_id, platform, status, score, reason, evidence.')
        k = key(decision)
        if k in seen:
            raise ValueError('Only one decision per job in a batch.')
        seen.add(k)
        if k not in data['jobs']:
            raise ValueError(f'Job {k} is not registered in this run.')
        status = decision.get('status')
        if status not in STATUSES:
            raise ValueError(f'Unknown outcome: {status}')
        score = decision.get('score')
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int,float)) or not 0 <= score <= 100):
            raise ValueError('Score must be between 0 and 100.')
        reason = decision.get('reason')
        if status == 'needs_input' and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError('needs_input requires a short reason.')
        if reason is not None and not isinstance(reason, str):
            raise ValueError('reason must be text.')
        if status == 'applied' and score is None:
            raise ValueError('applied requires a score.')
        extra = {name:value for name,value in [('score',score),('notes',reason),('evidence',decision.get('evidence'))] if value is not None}
        e = event_for(data['jobs'][k], run, status, **extra)
        if e['event_id'] in existing_ids:
            continue
        previous = [x for x in events if matches(x, data['jobs'][k])]
        if any(x.get('event') == 'applied' for x in previous):
            raise ValueError(f'{k} already has a confirmed application; do not overwrite or reapply.')
        pending.append(e)
    ledger.append_events(pending)
    return render(run)


def recent_manifests(run, current):
    """Actual registered runs, not arbitrary folders or number of renders."""
    candidates = []
    current_path = Path(run).resolve()
    cutoff = datetime.fromisoformat(current['created_at'])
    for path in current_path.parent.glob('*/report-input.json'):
        if path.parent.resolve() == current_path:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        stamp = datetime.fromisoformat(data['created_at'])
        if stamp <= cutoff:
            candidates.append((stamp, str(path.parent), data))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [data for _, _, data in candidates[:ATTENTION_LOOKBACK_RUNS]]


def previous_attention(run, data, events):
    previous = recent_manifests(run, data)
    previous_ids = {item['run'] for item in previous}
    candidates = {}
    for old in previous:
        for job in old.get('jobs', {}).values():
            candidates.setdefault(key(job), dict(job))
    for event in events:
        if event.get('run') in previous_ids and event.get('event') == 'needs_input' and event.get('job_id') and event.get('platform'):
            job = {field: event[field] for field in FIELDS if event.get(field) is not None}
            job['job_id'] = str(job['job_id'])
            job.setdefault('title', 'Job ' + job['job_id'])
            job.setdefault('company', 'Not recorded')
            candidates.setdefault(key(job), job)
    carried = []
    current_jobs = list(data['jobs'].values())
    for job in candidates.values():
        if any(matches(job, present) for present in current_jobs):
            continue
        relevant = [e for e in events if e.get('job_id') and matches(e, job)]
        if any(e.get('event') in {'applied', 'interview', 'offer', 'rejected'} for e in relevant):
            continue
        decisions = [e for e in relevant if e.get('event') in STATUSES]
        if not decisions or decisions[-1]['event'] != 'needs_input':
            continue
        last = decisions[-1]
        if last.get('run') not in previous_ids:
            continue
        if any(matches(job, old) for old in carried):
            continue
        carried.append(dict(job, status='needs_input', reason=last.get('notes') or 'Input required.',
                            prior_run=last['run'], score=last.get('score')))
    # Run-scoped blockers are resolved in their originating run. A current-run
    # entry with the same ID supersedes an older mention, including resolution.
    seen = set(data.get('blockers', {}))
    blockers = []
    for old in previous:
        for item in run_blockers(old, events):
            if item['id'] not in seen:
                blockers.append(dict(item, prior_run=old['run']))
                seen.add(item['id'])
        seen.update(old.get('blockers', {}))
    return {'lookback_runs': ATTENTION_LOOKBACK_RUNS,
            'runs': [old['run'] for old in previous], 'jobs': carried, 'blockers': blockers}


def report(run):
    data = manifest(run)
    events = ledger.read_events()
    attention = previous_attention(run, data, events)
    jobs = []
    for job in data['jobs'].values():
        decisions = [e for e in events if e.get('job_id') and e.get('run') == data['run'] and
                     e.get('event') in STATUSES and key(e) == key(job)]
        row = dict(job)
        if decisions:
            last = decisions[-1]
            row.update(status=last['event'], score=last.get('score'), reason=last.get('notes'),
                       evidence=last.get('evidence'))
        else:
            row.update(prior_outcome(job, events, data['run'], data.get('assessment_policy')))
        if row['status'] == 'needs_input':
            row['attention_visible'] = not row.get('prior_run') or row['prior_run'] in attention['runs']
        jobs.append(row)
    counts = {status:sum(j['status'] == status for j in jobs)
              for status in (*sorted(FINAL), 'applying','unresolved','previously_resolved')}
    unresolved = counts['unresolved'] + counts['applying']
    issues = [s.get('reason') or s.get('error') or 'Collection incomplete'
              for s in data['sources'].values() if s.get('status', 'complete') != 'complete']
    blockers = run_blockers(data, events)
    state = 'incomplete' if unresolved or issues or blockers else 'complete'
    return {'run':data['run'], 'created_at':data['created_at'], 'status':state,
            'counts':counts, 'total':len(jobs), 'unresolved':unresolved,
            'collection_issues':issues, 'blockers':blockers, 'sources':data['sources'], 'jobs':jobs,
            'previous_attention':attention}


def md_text(value):
    """Keep untrusted job text literal inside Markdown tables and headings."""
    import re
    text = ' '.join(str(value if value is not None else '').split())
    text = html.escape(text, quote=False)
    return re.sub(r'([\\`*_[\]|])', r'\\\1', text)


def md_link(label, url):
    from urllib.parse import quote
    try:
        parsed = urlparse(str(url or ''))
    except ValueError:
        return md_text(label)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return md_text(label)
    return '[' + md_text(label) + '](' + quote(str(url), safe=':/?&=%#@+;,-._~') + ')'


def summary_lines(rep, run):
    counts = rep['counts']
    history = rep.get('previous_attention', {'runs': [], 'jobs': [], 'blockers': [], 'lookback_runs': ATTENTION_LOOKBACK_RUNS})
    needs = [j for j in rep['jobs'] if j['status'] == 'needs_input' and j.get('attention_visible', True)]
    older_hidden = sum(j['status'] == 'needs_input' and not j.get('attention_visible', True) for j in rep['jobs'])
    started = datetime.fromisoformat(rep['created_at']).astimezone(local_zone()).strftime('%d %b %Y, %H:%M')
    lines = ['# Job hunt report', '',
             '**Run:** ' + md_text(rep['run']) + '  ',
             '**Started:** ' + started + ' (' + str(local_zone()) + ')  ',
             '**Status:** ' + rep['status'].capitalize(), '', '## At a glance', '',
             '| This run | Count |', '| --- | ---: |',
             f"| Unique jobs registered | {rep['total']} |",
             f"| Applied | {counts['applied']} |",
             f"| Skipped | {counts['skipped']} |",
             f"| Needs your input, within the review window | {len(needs)} |",
             f"| Agent work unfinished | {rep['unresolved']} |",
             f"| Previously handled | {counts['previously_resolved']} |"]
    if older_hidden:
        lines.append(f'| Older blocked jobs, outside the review window | {older_hidden} |')
    lines += ['', 'Carryover items below are separate from this run’s job totals.', '']

    if needs or history['jobs'] or rep['blockers'] or history['blockers']:
        lines += ['## Needs your input', '', '| Job | Company | What is needed | From run |', '| --- | --- | --- | --- |']
        for j in needs + history['jobs']:
            origin = j.get('prior_run') or 'This run'
            lines.append('| ' + ' | '.join([md_link(j['title'], j.get('url')), md_text(j['company']),
                         md_text(j.get('reason') or 'Input required; no reason recorded.'), md_text(origin)]) + ' |')
        if rep['blockers'] or history['blockers']:
            lines += ['', '### Run or account blockers', '']
            for b in rep['blockers'] + history['blockers']:
                origin = b.get('prior_run') or 'This run'
                lines.append('- ' + md_text(b['reason']) + ' — ' + md_text(origin) + ' (ID: ' + md_text(b['id']) + ')')
        lines += ['']

    lines += ['## Search coverage', '',
              '| Platform | Keyword | Location / scope | Posted within | Search status | Jobs returned |',
              '| --- | --- | --- | --- | --- | ---: |']
    platform_names = {'linkedin': 'LinkedIn', 'workday': 'Workday', 'remotive': 'Remotive',
                      'weworkremotely': 'We Work Remotely', 'greenhouse': 'Greenhouse'}
    filters = {'24h': 'Past 24 hours', 'week': 'Past week', 'month': 'Past month', 'any': 'Any time'}
    for source in rep['sources'].values():
        platform = source.get('platform', 'Not recorded')
        location = source.get('location', 'Not recorded')
        if source.get('remote_only'):
            location += ' · remote only'
        values = [platform_names.get(platform, platform), source.get('keyword', 'Not recorded'), location,
                  filters.get(source.get('posted_within'), source.get('posted_within', 'Not recorded')),
                  source.get('status', 'Not recorded'), source.get('matched_jobs', source.get('new_jobs', '—'))]
        lines.append('| ' + ' | '.join(md_text(x) for x in values) + ' |')
    if not rep['sources']:
        lines.append('| No collection source registered | — | — | — | — | — |')
    coverage = sorted({f"{platform_names.get(s.get('platform'), s.get('platform'))}: {s['coverage']}"
                       for s in rep['sources'].values() if s.get('coverage')})
    lines += ['', *['- ' + md_text(c) for c in coverage]]
    lines += ['', 'Search details reflect saved source metadata. “Not recorded” is not inferred from current search settings.', '']
    if rep['collection_issues']:
        lines += ['### Collection incomplete', '']
        lines += ['- ' + md_text(issue) for issue in rep['collection_issues']]
        lines += ['']

    applied = [j for j in rep['jobs'] if j['status'] == 'applied']
    if applied:
        lines += [f'## Applied ({len(applied)})', '']
        for j in applied:
            lines += ['### ' + md_link(j['title'], j.get('url')) + ' — ' + md_text(j['company']), '']
            details = []
            if j.get('location'):
                details.append('**Location:** ' + md_text(j['location']))
            if j.get('score') is not None:
                details.append(f"**Fit score:** {j['score']:g}/100")
            if details:
                lines += [' · '.join(details), '']
            if j.get('reason'):
                lines += [md_text(j['reason']), '']
            if j.get('evidence'):
                evidence = j['evidence']
                if isinstance(evidence, str):
                    from urllib.parse import quote
                    path = Path(evidence)
                    path = path if path.is_absolute() else ROOT / path
                    if path.resolve().is_relative_to(ROOT):
                        link = quote(os.path.relpath(path, Path(run).resolve()), safe='/.-_')
                        lines += ['**Evidence:** [View confirmation](' + link + ')', '']
                    else:
                        lines += ['**Evidence:** ' + md_text(evidence), '']
                else:
                    lines += ['**Evidence:** ' + md_text(json.dumps(evidence, ensure_ascii=False)), '']

    sections = [('Agent work remaining', {'applying', 'unresolved'}),
                ('Skipped', {'skipped'}), ('Previously handled', {'previously_resolved'})]
    for title, statuses in sections:
        rows = [j for j in rep['jobs'] if j['status'] in statuses]
        if not rows:
            continue
        lines += [f'## {title} ({len(rows)})', '']
        if title == 'Agent work remaining':
            lines += ['These jobs still need agent evaluation or application confirmation; they are not requests for your decision.', '']
        elif title == 'Previously handled':
            lines += ['Encountered in this run, but applied for or skipped earlier.', '']
        lines += ['| Job | Company | Reason / state |', '| --- | --- | --- |']
        for j in rows:
            reason = j.get('reason') or ('Awaiting evaluation' if j['status'] == 'unresolved' else
                     'Application confirmation outstanding' if j['status'] == 'applying' else 'Not recorded')
            lines.append('| ' + ' | '.join([md_link(j['title'], j.get('url')), md_text(j['company']), md_text(reason)]) + ' |')
        lines += ['']
    lines += ['## Report scope', '',
              'This report describes one run. Open needs-input items from the previous two registered runs are included separately, unless already resolved or skipped. Re-rendering a report does not extend that window.', '',
              '**Previous runs checked:** ' + (', '.join(md_text(r) for r in history['runs']) or 'None available') + '.', '',
              'Older items remain in the ledger and their original run reports; leaving the review window does not mark them resolved. Carryover notes describe the state observed in the originating run, not a fresh browser check.', '']
    return lines


def render(run):
    rep = report(run)
    run = Path(run)
    atomic_json(run / 'report.json', rep)
    lines = summary_lines(rep, run)
    (run / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'status':rep['status'], 'counts':rep['counts'],
            'blockers':len(rep['blockers']), 'report_file':str(run/'report.json'), 'summary_file':str(run/'summary.md')}


def finalize(run):
    """Generate the final report; completion describes work, not message delivery.

    Call under run_lock, as with collect/record/render.
    """
    output = render(run)
    if output['status'] == 'complete':
        stamp = {'platform': 'run', 'job_id': run_id(run)}
        ledger.append_events([event_for(stamp, run, 'run_complete')])
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for command in ['collect','record','preview','blocker','finalize']:
        sp = sub.add_parser(command)
        sp.add_argument('--run', required=True, type=Path)
    sub.choices['collect'].add_argument('--jobs', action='append', required=True, type=Path)
    sub.choices['record'].add_argument('--decisions', required=True)
    bp = sub.choices['blocker']
    bp.add_argument('--id', required=True)
    action = bp.add_mutually_exclusive_group(required=True)
    action.add_argument('--reason')
    action.add_argument('--resolve', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    try:
        with run_lock(a.run):
            if a.command == 'collect':
                result = collect(a.run, a.jobs)
            elif a.command == 'blocker':
                result = blocker(a.run, a.id, a.reason, a.resolve)
            elif a.command == 'record':
                payload = json.loads(sys.stdin.read() if a.decisions == '-' else Path(a.decisions).read_text(encoding='utf-8'))
                result = record(a.run, payload)
            elif a.command == 'finalize':
                result = finalize(a.run)
            else:
                result = render(a.run)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status':'error', 'reason':str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
