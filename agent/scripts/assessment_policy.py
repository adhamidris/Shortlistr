"""Reconsider skips only when the user explicitly changes the assessment policy version."""
import ledger

from runtime import settings
VERSION = settings().get('assessment_policy_version', 'initial')


def recheck_ids(platform):
    latest = {}
    applied = set()
    for event in ledger.read_events():
        if event.get('platform') != platform or not event.get('job_id'):
            continue
        job_id = str(event['job_id'])
        if event.get('event') in {'applied', 'interview', 'offer', 'rejected'}:
            applied.add(job_id)
        if event.get('event') in {'applied', 'skipped', 'needs_input', 'applying'}:
            latest[job_id] = event
    return {job_id for job_id, event in latest.items()
            if job_id not in applied and event['event'] == 'skipped'
            and event.get('assessment_policy') != VERSION}
