"""Excel-compatible submission list; preserve the user's manual follow-up columns."""
import csv
import os
from pathlib import Path
import tempfile

FIELDS = ['company', 'title', 'applied_at', 'url', 'platform', 'job_id',
          'email_status', 'latest_email_at', 'email_note', 'next_action',
          'manual_status', 'manual_notes']


def refresh(events, path):
    path = Path(path)
    previous = {}
    if path.exists():
        with path.open(encoding='utf-8-sig', newline='') as stream:
            previous = {(r['platform'], r['job_id']): r for r in csv.DictReader(stream)}
    from followup import current_rows
    email_rows = {(r['platform'], r['job_id']): r for r in current_rows(events)}
    rows = {}
    for event in events:
        if event.get('event') != 'applied':
            continue
        key = (event.get('platform', ''), str(event.get('job_id') or event.get('job_hash', '')))
        if key in rows:
            continue
        old = previous.get(key, {})
        email = email_rows.get(key, {})
        rows[key] = dict(company=event.get('company', ''), title=event.get('title', ''),
                         applied_at=event.get('ts', ''), url=event.get('url', ''),
                         platform=key[0], job_id=key[1],
                         email_status=email.get('status', 'applied'),
                         latest_email_at=email.get('latest_update', ''),
                         email_note=email.get('latest_note', ''),
                         next_action=email.get('next_action', ''),
                         manual_status=old.get('manual_status', ''),
                         manual_notes=old.get('manual_notes', ''))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.csv')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows.values():
                writer.writerow({k: ("'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@'))
                                     else v) for k, v in row.items()})
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(rows)


if __name__ == '__main__':
    import ledger
    destination = ledger.LEDGER.parent.parent / 'data/applied-jobs.csv'
    print(f'{refresh(ledger.read_events(), destination)} applications: {destination}')
