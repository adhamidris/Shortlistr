"""One factual current-action card; no tool history or model summarization."""
from html import escape
from pathlib import PurePath
import re
import shlex
from urllib.parse import urlparse


def short(value, limit=600):
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def shell_label(command):
    # Parse only enough to identify an executable. Never display arbitrary arguments,
    # assignments, inline code or paths. Recognize a leading `cd ... &&` explicitly.
    try:
        words = shlex.split(command)
    except ValueError:
        return '💻 Shell command'
    if len(words) >= 4 and words[0] == 'cd' and words[2] == '&&':
        words = words[3:]
    if not words:
        return '💻 Shell command'
    executable = PurePath(words[0]).name
    if executable in {'python', 'python3', 'node', 'bash', 'sh', 'zsh'} and len(words) > 1:
        if re.fullmatch(r'[\w./-]+\.(?:py|js|sh)', words[1]):
            script = PurePath(words[1]).name
            if script == 'run_report.py' and len(words) > 2:
                actions = {'collect': 'Registering collected jobs', 'record': 'Recording job outcomes',
                           'finalize': 'Preparing run summary', 'preview': 'Checking run summary',
                           'blocker': 'Updating a blocker'}
                if words[2] in actions:
                    return '📝 ' + actions[words[2]] + '\n' + script
            return '💻 Running script\n' + short(script)
    if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', executable):
        return '💻 Shell command\n' + executable
    return '💻 Shell command'


def tool_label(frame):
    name = frame.get('name') or 'Tool'
    args = frame.get('input') if isinstance(frame.get('input'), dict) else {}
    suffix = name.split('__')[-1]
    provider = name.split('__')[-2] if '__' in name else ''
    if suffix == 'search_jobs' and provider in {'linkedin', 'remotive', 'weworkremotely', 'greenhouse'}:
        source = provider
        names = {'linkedin': 'LinkedIn', 'remotive': 'Remotive',
                 'weworkremotely': 'We Work Remotely', 'greenhouse': 'Greenhouse'}
        title = '🔎 Searching ' + names.get(source, 'job sources')
        details = [short(args[k]) for k in ('keyword', 'location', 'posted_within') if args.get(k)]
        periods = {'week': 'Past week', '24h': 'Past day', 'month': 'Past month', 'any': 'Any time'}
        details = [periods.get(v, v) for v in details]
        if args.get('remote_only'):
            details.append('Remote only')
        return title + ('\n' + ' · '.join(details) if details else '')
    if (provider == 'linkedin' and suffix == 'easy_apply') or (provider in {'greenhouse'} and suffix == 'apply_job'):
        platform = {'linkedin': 'LinkedIn Easy Apply', 
                    'greenhouse': 'Greenhouse application'}[provider]
        action = args.get('action', 'inspect')
        verbs = {'inspect': ('📋', 'Inspecting'), 'fill': ('✍️', 'Filling'),
                 'answer': ('✍️', 'Filling'), 'continue': ('➡️', 'Continuing'),
                 'submit': ('📨', 'Submitting')}
        icon, verb = verbs.get(action, ('🔧', 'Working on'))
        return f'{icon} {verb} {platform}'
    if name in {'Bash', 'Shell'}:
        return shell_label(args.get('command', ''))
    names = {
        'browser_navigate': '🌐 Opening page', 'browser_snapshot': '📋 Inspecting page',
        'browser_click': '🖱 Clicking page element', 'browser_fill_form': '✍️ Filling form',
        'browser_evaluate': '🔧 Running browser code', 'browser_run_code_unsafe': '🔧 Running browser code',
        'browser_wait_for': '⏳ Waiting for page', 'browser_file_upload': '📎 Uploading file',
        'browser_tabs': '🌐 Managing tabs', 'browser_type': '✍️ Entering text',
        'browser_press_key': '⌨️ Pressing key', 'browser_take_screenshot': '📷 Inspecting screenshot',
        'browser_find': '🔎 Finding page element', 'browser_select_option': '✍️ Selecting option',
        'scan_application_mail': '📬 Checking relevant email', 'read_application_mail': '📨 Reading selected email',
        'Read': '📄 Reading file', 'Write': '📝 Writing file', 'Edit': '📝 Editing file',
        'TodoList': '📋 Updating task list', 'AskUserQuestion': '💬 Asking a question',
    }
    label = names.get(suffix, '🔧 ' + short(name))
    if suffix == 'browser_navigate':
        try:
            host = urlparse(str(args.get('url', ''))).hostname
            if host:
                label += '\n' + short(host)
        except ValueError:
            pass
    return label


def snapshot(sid, turn):
    # Use the latest frame for each ID: a completed update supersedes running.
    frames = {}
    for step in turn.get('steps', []):
        for frame in step.get('frames', []):
            if frame.get('kind') == 'tool':
                key = frame.get('toolCallId') or frame.get('frameId') or ('anonymous', len(frames))
                frames[key] = frame
    calls = [] if turn.get('endedAt') else [
        {'label': tool_label(frame), 'state': frame.get('state', 'pending')}
        for frame in frames.values()
    ]
    active = [c for c in calls if c['state'] == 'running']
    finished = [c for c in calls if c['state'] in {'done', 'completed', 'error', 'failed', 'interrupted'}]
    return {'session': sid, 'turn': turn.get('turnId'), 'ended': bool(turn.get('endedAt')),
            'calls': active or finished[-1:]}


def activity_html(data, *, connected, running, waiting):
    if not connected or not running or not data or data.get('ended'):
        return None
    calls = data.get('calls', [])
    if not calls:
        return None  # Telegram's typing indicator covers periods without tool activity.
    call = calls[-1]
    title, _, detail = call['label'].partition('\n')
    if call['state'] != 'running':
        marker = '✗' if call['state'] in {'error', 'failed'} else '–' if call['state'] == 'interrupted' else '✓'
        title = marker + ' ' + title + ' · ' + ('Failed' if marker == '✗' else 'Interrupted' if marker == '–' else 'Done')
    text = '<b>' + escape(title) + '</b>'
    if detail:
        text += '\n' + escape(detail)
    if len(calls) > 1:
        text += f'\n<i>{len(calls)} tools currently running</i>'
    return text
