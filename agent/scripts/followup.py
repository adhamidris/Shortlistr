"""Reconcile evidence into the existing ledger; generate a current application tracker."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ledger
import run_report as reports
ROOT = reports.ROOT

EVENTS={'confirmation','application_viewed','resume_downloaded','assessment','interview','rejected','offer','needs_response'}


def run_path(path):
    path=Path(path).resolve()
    if not path.is_relative_to((ROOT/'runs').resolve()):raise ValueError('Use a path inside runs/.')
    return path


def applications(events):
    jobs={}
    for e in events:
        if e.get('event')=='applied' and e.get('job_id'):
            key=(e.get('platform','linkedin'),str(e['job_id']))
            if key not in jobs:jobs[key]=e
    return jobs


def record(run, updates, ignored_message_ids=None):
    import outlook_mail as mail
    from linkedin import utc_now
    run=run_path(run)
    with reports.run_lock(run),mail.mail_lock():
        events=ledger.read_events();jobs=applications(events);state=mail.load_state()
        pending=[];consumed=set();ignored=set(ignored_message_ids or [])
        for item in updates:
            key=(item.get('platform','linkedin'),str(item.get('job_id','')))
            if key not in jobs:raise ValueError(f'No recorded application for {key[0]}:{key[1]}; leave this message pending and record valid matches separately.')
            status=item.get('status')
            if status not in EVENTS:raise ValueError('Unsupported follow-up status.')
            reason=item.get('reason','').strip()
            if not reason or len(reason)>600:raise ValueError('Provide a concise evidence-based reason (1–600 characters).')
            if item.get('message_id'):
                mid=item['message_id']
                if mid in ignored:raise ValueError('A message cannot be both ignored and recorded.')
                if mid not in state['messages']:raise ValueError('Message must come from the scanned metadata queue.')
                body=mail.DATA/'bodies'/(hashlib.sha256(mid.encode()).hexdigest()+'.json')
                if not body.exists():raise ValueError('Read the selected message before recording an outcome; subject alone is insufficient.')
                prior=state['processed'].get(mid)
                if prior and prior.get('status')=='ignored':raise ValueError('Message was previously ignored; reconcile before reclassifying.')
                evidence=str(body);source='outlook';source_id=mid
                occurred_at=state['messages'][mid]['received_at'];consumed.add(mid)
            else:raise ValueError('Supply a read message_id as evidence.')
            event=reports.event_for(jobs[key],run,status,notes=reason,evidence=evidence)
            event.update(source=source,source_id=source_id,occurred_at=occurred_at)
            event['event_id']=hashlib.sha256(json.dumps([source,source_id,key,status]).encode()).hexdigest()
            pending.append(event)
        if any(mid not in state['messages'] for mid in ignored):raise ValueError('Unknown ignored message ID.')
        if any(state['processed'].get(mid,{}).get('status')=='recorded' for mid in ignored):raise ValueError('Cannot ignore a message already used as evidence.')
        count=ledger.append_events(pending)
        # Append first: a crash before queue acknowledgement is safely replayed by event ID.
        for mid in consumed:state['processed'][mid]={'status':'recorded','at':utc_now()}
        for mid in ignored:state['processed'][mid]={'status':'ignored','at':utc_now()}
        mail.save_state(state)
        output=render(run)
        return {**output,'new_events':count,'ignored_messages':len(ignored)}


def current_rows(events):
    rows=[]
    for key,job in applications(events).items():
        related=[e for e in events if (e.get('platform','linkedin'),str(e.get('job_id','')))==key]
        updates=[e for e in related if e.get('event') in EVENTS]
        # Stages never regress on acknowledgements, activity, or older-arriving email.
        substantive=[e for e in updates if e['event'] in {'assessment','interview','rejected','offer'}]
        def when(e):return datetime.fromisoformat(e.get('occurred_at',e['ts']).replace('Z','+00:00'))
        terminal=[e for e in substantive if e['event'] in {'rejected','offer'}]
        if terminal:stage=max(terminal,key=when)['event']
        elif substantive:stage=max(substantive,key=lambda e:{'assessment':1,'interview':2}[e['event']])['event']
        else:stage='applied'
        latest=max(updates,key=when) if updates else job
        rows.append({'platform':key[0],'job_id':key[1],'company':job.get('company',''),
                     'title':job.get('title',''),'url':job.get('url',''),'applied_at':job['ts'],
                     'status':stage,'activity':', '.join(sorted({e['event'] for e in updates if e['event'] in {'application_viewed','resume_downloaded'}})),
                     'latest_update':latest.get('occurred_at',latest['ts']) if updates else '',
                     'latest_note':latest.get('notes','') if updates else '',
                     'next_action':'Review latest correspondence' if stage in {'assessment','interview','offer'} or (latest.get('event')=='needs_response') else ''})
    return rows


def render(run):
    from linkedin import atomic_write
    run=run_path(run);events=ledger.read_events();rows=current_rows(events)
    from application_sheet import refresh
    refresh(events, ROOT/'data/applied-jobs.csv')
    updates=[e for e in events if e.get('run')==reports.run_id(run) and e.get('event') in EVENTS]
    summary_path=run/'tracking-summary.md'
    lines=['# Application updates','',f'{len(updates)} recorded updates. {len(rows)} applications in tracker.','']
    lines += [f"- {e.get('company','')} — {e.get('title','')}: {e['event']} — {e.get('notes','')}" for e in updates]
    atomic_write(summary_path,'\n'.join(lines)+'\n')
    return {'status':'complete','updates':len(updates),'tracker_file':str(ROOT/'data/applied-jobs.csv'),
            'summary_file':str(summary_path),'note':'Rendering complete; source scans/triage may still be pending. Check scan results before sending.'}
