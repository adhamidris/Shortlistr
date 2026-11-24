"""Incremental metadata collection for explicitly requested email follow-up."""
import asyncio
from datetime import datetime, timezone
import json
from urllib.parse import urlencode
import httpx
import ledger
import outlook_mail as mail
from linkedin import atomic_write


async def scan(output):
    with mail.mail_lock():
        token, account = mail.auth()
        state = mail.load_state()
        if state.get('account') not in (None, account):
            raise ValueError('Mailbox differs from saved history.')
        state['account'] = account
        summary = {'status': 'in_progress', 'results_file': str(output), 'folders_completed': []}
        def checkpoint():
            pending = [m for mid, m in state['messages'].items() if mid not in state['processed']]
            summary['pending_messages'] = len(pending)
            atomic_write(output, json.dumps({'summary': summary, 'messages': pending}, ensure_ascii=False, indent=2)+'\n')
            mail.save_state(state)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                for folder in ('inbox', 'junkemail'):
                    entry = state['folders'].setdefault(folder, {})
                    if not entry.get('id'):
                        entry['id'] = (await mail.graph_get(client, f'me/mailFolders/{folder}', token, {'$select': 'id'}))['id']
                    url = entry.get('cursor')
                    if not url:
                        times = [e['ts'] for e in ledger.read_events() if e.get('event') == 'applied']
                        if not times:
                            raise ValueError('No recorded applications to follow up.')
                        since = min(datetime.fromisoformat(t.replace('Z', '+00:00')) for t in times).astimezone(timezone.utc).isoformat()
                        query = urlencode({'$select': 'id,from,subject,receivedDateTime',
                                           '$filter': 'receivedDateTime ge '+since, '$top': '50'})
                        url = f'me/mailFolders/{folder}/messages/delta?{query}'
                    while url:
                        result = await mail.graph_get(client, url, token)
                        for msg in result.get('value', []):
                            if '@removed' in msg:
                                continue
                            state['messages'][msg['id']] = {
                                'id': msg['id'], 'sender': msg.get('from', {}).get('emailAddress', {}),
                                'subject': msg.get('subject', ''), 'received_at': msg.get('receivedDateTime'),
                                'folder': folder}
                        cursor = result.get('@odata.nextLink') or result.get('@odata.deltaLink')
                        if not cursor:
                            raise ValueError('Missing mail continuation cursor; retry this scan.')
                        entry['cursor'] = cursor
                        checkpoint()
                        url = result.get('@odata.nextLink')
                    summary['folders_completed'].append(folder)
            summary['status'] = 'complete'
        except asyncio.CancelledError:
            summary.update(status='interrupted', reason='Saved metadata and cursors; resume this manual check.')
            raise
        except Exception as exc:
            summary.update(status='partial', reason=mail.safe_error(exc))
        finally:
            checkpoint()
        return summary
