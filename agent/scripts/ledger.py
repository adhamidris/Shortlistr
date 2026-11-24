#!/usr/bin/env python3
"""Append-only JSONL ledger for the job-hunt agent.

Usage:
    ledger.py check --platform P [--job-id ID] [--company C --title T]
    ledger.py log --event EVENT --platform P [--job-id ID] [--company C]
                  [--title T] [--url U] [--score N] [--notes TEXT] [--run RUN_ID]
    ledger.py summary

`check` exits 0 and prints "new" if the job is not in the ledger,
prints the most recent matching event and exits 1 if it is.
"""
import argparse
import hashlib
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent.parent / "ledger" / "applications.jsonl"


def normalize(text):
    return " ".join((text or "").lower().split())


def job_hash(platform, job_id, company, title):
    if job_id:
        key = f"{normalize(platform)}:{normalize(job_id)}"
    else:
        key = f"{normalize(platform)}:{normalize(company)}:{normalize(title)}"
    return hashlib.sha1(key.encode()).hexdigest()


def read_events():
    if not LEDGER.exists():
        return []
    events = []
    with LEDGER.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def append_events(events):
    """Append under a lock; stable event IDs make interrupted batch retries safe."""
    import file_lock as fcntl
    events = list(events)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open('a+', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        existing = [json.loads(line) for line in f if line.strip()]
        ids = {e.get('event_id') for e in existing if e.get('event_id')}
        added = 0
        appended = []
        for event in events:
            if event.get('event_id') and event['event_id'] in ids:
                continue
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
            if event.get('event_id'):
                ids.add(event['event_id'])
            added += 1
            appended.append(event)
        f.flush()
        os.fsync(f.fileno())
        if any(e.get('event') == 'applied' for e in events):
            from application_sheet import refresh
            try:
                refresh(existing + appended, LEDGER.parent.parent / 'data/applied-jobs.csv')
            except Exception as exc:
                # Submission evidence is already durable; export failure must not invite reapplication.
                print(f'Application recorded; spreadsheet refresh failed: {exc}. '
                      'Run agent/scripts/application_sheet.py to refresh.', file=sys.stderr)
    return added


def cmd_check(args):
    target = job_hash(args.platform, args.job_id, args.company, args.title)
    matches = [e for e in read_events() if e.get("job_hash") == target]
    if not matches:
        print("new")
        return 0
    latest = matches[-1]
    print(json.dumps(latest, ensure_ascii=False))
    return 1


def cmd_log(args):
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": args.event,
        "platform": args.platform,
        "job_hash": job_hash(args.platform, args.job_id, args.company, args.title),
    }
    for field in ("job_id", "company", "title", "url", "score", "notes", "run"):
        value = getattr(args, field)
        if value is not None:
            event[field] = value
    append_events([event])
    print(json.dumps(event, ensure_ascii=False))
    return 0


def cmd_summary(_args):
    events = read_events()
    counts = {}
    for e in events:
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    print(f"total events: {len(events)}")
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count}")
    applied = [e for e in events if e["event"] == "applied"]
    if applied:
        print(f"\napplied ({len(applied)}):")
        for e in applied:
            print(f"  {e['ts'][:10]}  {e.get('company', '?')} — {e.get('title', '?')}  ({e['platform']})")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("check", "log"):
        p = sub.add_parser(name)
        p.add_argument("--platform", required=True)
        p.add_argument("--job-id", dest="job_id")
        p.add_argument("--company")
        p.add_argument("--title")

    log_p = sub.choices["log"]
    log_p.add_argument("--event", required=True)
    log_p.add_argument("--url")
    log_p.add_argument("--score", type=float)
    log_p.add_argument("--notes")
    log_p.add_argument("--run")

    sub.add_parser("summary")
    args = parser.parse_args()

    if args.command == "check":
        return cmd_check(args)
    if args.command == "log":
        return cmd_log(args)
    return cmd_summary(args)


if __name__ == "__main__":
    sys.exit(main())
