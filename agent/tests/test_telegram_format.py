from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from telegram_format import markdown_chunks, markdown_html

class Inspect(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.text, self.tags = [], '', []
    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        self.tags.append((tag, attrs))
    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError('Unbalanced tags')
    def handle_data(self, data):
        self.text += data

class FormatTests(unittest.TestCase):
    def test_bold_code_and_links_render_without_markdown_delimiters(self):
        html = markdown_html('## Ready\n\n**Bold** and `code <x>`\n\n[Site](https://example.com/?a=1&b=2)')
        self.assertIn('<b>Ready</b>', html)
        self.assertIn('<b>Bold</b>', html)
        self.assertIn('<code>code &lt;x&gt;</code>', html)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', html)
        self.assertNotIn('**', html)
        self.assertNotIn('##', html)

    def test_html_is_not_executed_and_local_paths_are_not_links(self):
        html = markdown_html('<script>alert(1)</script>\n\n[report](runs/report.csv)')
        self.assertNotIn('<script>', html)
        self.assertNotIn('href=', html)
        self.assertIn('report', html)

    def test_long_nested_format_and_emoji_chunks_are_balanced(self):
        parts = markdown_chunks('**' + ('😀<& text ' * 1500).rstrip() + '**', limit=200)
        visible = ''
        for part in parts:
            p = Inspect(); p.feed(part)
            self.assertFalse(p.stack)
            self.assertLessEqual(len(p.text.encode('utf-16-le'))//2, 200)
            visible += p.text
        self.assertEqual(visible, ('😀<& text ' * 1500).rstrip())

    def test_long_code_block_preserves_content(self):
        text = 'a < b && c > d\n' * 600
        parts = markdown_chunks('```python\n' + text + '```', limit=200)
        visible = ''
        for part in parts:
            p = Inspect(); p.feed(part)
            self.assertFalse(p.stack)
            self.assertIn(('pre', []), p.tags)
            visible += p.text
        self.assertEqual(visible.strip(), text.strip())

    def test_table_becomes_readable_rows_not_markdown_pipes(self):
        html = markdown_html('| Job | Status |\n|---|---|\n| Engineer | Applied |')
        self.assertIn('Job: Engineer', html)
        self.assertIn('Status: Applied', html)
        self.assertNotIn('|', html)

if __name__ == '__main__':
    unittest.main()
