"""Render Markdown into Telegram's restricted HTML, then split balanced messages."""
from runtime import local_zone
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from markdown_it import MarkdownIt

MD = MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough'])


def inline(tokens):
    out, links = [], []
    for t in tokens or []:
        kind = t.type
        if kind == 'text':
            out.append(escape(t.content))
        elif kind in ('softbreak', 'hardbreak'):
            out.append('\n')
        elif kind == 'code_inline':
            out.append('<code>' + escape(t.content) + '</code>')
        elif kind in ('strong_open', 'strong_close', 'em_open', 'em_close', 's_open', 's_close'):
            tag = {'strong': 'b', 'em': 'i', 's': 's'}[kind.rsplit('_', 1)[0]]
            out.append('<' + ('/' if t.nesting == -1 else '') + tag + '>')
        elif kind == 'link_open':
            href = t.attrGet('href') or ''
            valid = urlparse(href).scheme.lower() in {'http', 'https', 'mailto'}
            links.append(valid)
            if valid:
                out.append('<a href="' + escape(href, quote=True) + '">')
        elif kind == 'link_close':
            if links and links.pop():
                out.append('</a>')
        elif kind == 'image':
            out.append(escape(t.content or 'Image'))
        else:
            out.append(escape(t.content))
    return ''.join(out)


def markdown_html(text):
    tokens = MD.parse(text)
    out, lists = [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        kind = t.type
        if kind == 'inline':
            out.append(inline(t.children))
        elif kind in ('fence', 'code_block'):
            out.append('<pre>' + escape(t.content.rstrip('\n')) + '</pre>\n\n')
        elif kind == 'heading_open':
            out.append('<b>')
        elif kind == 'heading_close':
            out.append('</b>\n\n')
        elif kind == 'paragraph_close':
            out.append('\n' if lists else '\n\n')
        elif kind in ('bullet_list_open', 'ordered_list_open'):
            lists.append(int(t.attrGet('start') or 1) if kind == 'ordered_list_open' else None)
        elif kind in ('bullet_list_close', 'ordered_list_close'):
            if lists:
                lists.pop()
            if not lists:
                out.append('\n')
        elif kind == 'list_item_open':
            index = lists[-1] if lists else None
            out.append('  ' * max(0, len(lists)-1) + (f'{index}. ' if index is not None else '• '))
            if index is not None:
                lists[-1] += 1
        elif kind == 'blockquote_open':
            out.append('› ')
        elif kind == 'hr':
            out.append('────────\n\n')
        elif kind == 'table_open':
            rows, row = [], []
            while i + 1 < len(tokens):
                i += 1
                cell = tokens[i]
                if cell.type == 'table_close':
                    break
                if cell.type == 'tr_open':
                    row = []
                elif cell.type == 'inline':
                    row.append(inline(cell.children))
                elif cell.type == 'tr_close':
                    rows.append(row)
            if rows:
                headers = rows[0]
                for row in rows[1:]:
                    out.append('• ' + ' · '.join(f'{headers[n]}: {value}' if n < len(headers) else value for n, value in enumerate(row)) + '\n')
                out.append('\n')
        elif kind == 'html_block':
            out.append(escape(t.content))
        i += 1
    return ''.join(out).strip()


class SplitHTML(HTMLParser):
    """Count rendered UTF-16 units; close/reopen formatting across boundaries."""
    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.stack, self.current, self.parts = [], [], []
        self.count = 0

    def flush(self):
        if self.count:
            self.parts.append(''.join(self.current) + ''.join('</' + tag + '>' for tag, _ in reversed(self.stack)))
        self.current = [opening for _, opening in self.stack]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag not in {'b', 'i', 's', 'code', 'pre', 'a'}:
            return
        opening = '<' + tag + ''.join(' ' + k + '="' + escape(v or '', quote=True) + '"' for k, v in attrs if tag == 'a' and k == 'href') + '>'
        self.current.append(opening)
        self.stack.append((tag, opening))

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            self.current.append('</' + tag + '>')
            self.stack.pop()

    def handle_data(self, data):
        # Character iteration avoids cutting surrogate pairs, entities or markup.
        for char in data:
            units = 2 if ord(char) > 0xffff else 1
            if self.count + units > self.limit:
                self.flush()
            self.current.append(escape(char))
            self.count += units


def html_chunks(html, limit=3500):
    parser = SplitHTML(limit)
    parser.feed(html)
    parser.close()
    parser.flush()
    return parser.parts


def markdown_chunks(text, limit=3500):
    return html_chunks(markdown_html(text), limit)


def model_label(config, models):
    alias = config.get('model')
    return next((m['display_name'] for m in models if m['model'] == alias), alias or 'Not set')


def settings_html(config, models):
    permission = {'yolo': 'Ask When Needed', 'manual': 'Ask for approval', 'auto': 'Never Ask'}.get(config.get('permission_mode'), 'Kimi default')
    return ('<b>Session settings</b>\n\n'
            '<b>Model:</b> ' + escape(model_label(config, models)) + '\n'
            '<b>Reasoning:</b> ' + escape(str(config.get('thinking', 'Default')).capitalize()) + '\n'
            '<b>Permissions:</b> ' + escape(permission))


def remaining(quotas, key):
    ratio = quotas.get(key, {}).get('usedRatio')
    return f'{max(0, 1-ratio)*100:.1f}% left' if isinstance(ratio, (float, int)) else 'Unavailable'


def status_html(state, config, models, usage, quotas):
    def compact(n):
        return f'{n/1_000_000:.1f}M' if n >= 1_000_000 else f'{n/1000:.1f}k'
    current, maximum = usage.get('context_tokens'), usage.get('max_context_tokens')
    context = f'{compact(current)} / {compact(maximum)} · {current/maximum:.0%}' if maximum and current is not None else 'Unavailable'
    return ('<b>Kimi · ' + escape(state) + '</b>\n\n'
            '<b>Model:</b> ' + escape(model_label(config, models)) + '\n'
            '<b>Reasoning:</b> ' + escape(str(config.get('thinking', 'Default')).capitalize()) + '\n'
            '<b>Context:</b> ' + context + '\n\n'
            '<b>5-hour allowance:</b> ' + remaining(quotas, 'limit5h') + '\n'
            '<b>Monthly allowance:</b> ' + remaining(quotas, 'monthTotal'))


def usage_html(quotas, now=None):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    zone = local_zone()
    now = now or datetime.now(zone)
    sections = ['<b>Account usage</b>']
    for key, label in [('limit5h', '5-hour allowance'), ('monthTotal', 'Monthly allowance'), ('monthCode', 'Monthly coding allowance')]:
        q = quotas.get(key)
        if not q:
            continue
        section = '<b>' + label + '</b>\n' + remaining(quotas, key)
        if q.get('resetAt'):
            try:
                reset = datetime.fromisoformat(q['resetAt'].replace('Z', '+00:00')).astimezone(zone)
                day = 'Today' if reset.date() == now.date() else reset.strftime('%d %b')
                section += '\nResets ' + day + ' at ' + reset.strftime('%H:%M')
            except ValueError:
                pass
        sections.append(section)
    if len(sections) == 1:
        sections.append('Kimi’s usage service is temporarily unavailable.')
    else:
        sections.append(f'<i>Reset times: {escape(str(local_zone()))}.</i>')
    return '\n\n'.join(sections)
