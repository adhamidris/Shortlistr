"""Browser fixtures use an isolated context: no LinkedIn requests or real submissions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
sys.path.insert(0,str(Path(__file__).parents[1] / 'scripts'))
from easy_apply import EasyApply, version, DOM, matches_job
from playwright.async_api import async_playwright


class FormTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='chrome', headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        await self.context.route('**/*',lambda route:route.fulfill(status=200,body='<html></html>',content_type='text/html'))
        await self.page.goto('https://www.linkedin.com/jobs/view/123/')
        self.tmp = tempfile.TemporaryDirectory()
        self.adapter = EasyApply(self.tmp.name)

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.pw.stop()
        self.tmp.cleanup()

    async def form(self,body):
        await self.page.set_content('<div role="dialog" aria-label="Apply to Test">'+body+'</div>')
        return await self.adapter.execute(self.browser,'123')

    async def test_continue_never_submits_and_stale_version_never_fills(self):
        r=await self.form('<label>Name<input value="Original"></label><button onclick="window.clicked=true">Submit application</button>')
        self.assertEqual(r['status'],'ready_to_submit')
        stale=await self.adapter.execute(self.browser,'123','continue',{'f1':'Changed'},'stale')
        self.assertEqual(stale['status'],'stale_form')
        self.assertEqual(await self.page.locator('input').input_value(),'Original')
        r=await self.adapter.execute(self.browser,'123','continue',{'f1':'Changed'},r['form_version'])
        self.assertEqual(r['status'],'ready_to_submit')
        self.assertFalse(await self.page.evaluate('!!window.clicked'))
        self.assertEqual(await self.page.locator('input').input_value(),'Changed')

    async def test_choice_labels_batch_and_inline_validation(self):
        r=await self.form('''<fieldset role="radiogroup"><legend>Cloud experience</legend>
        <label><input type="radio" name="cloud" aria-label="Cloud experience"><p>Yes</p></label>
        <label><input type="radio" name="cloud" aria-label="Cloud experience"><p>No</p></label></fieldset>
        <label>Notice<input></label><p id="error"></p><button onclick="document.getElementById('error').textContent='Invalid input'">Next</button>''')
        self.assertEqual([o['label'] for o in r['fields'][0]['options']],['Yes','No'])
        r=await self.adapter.execute(self.browser,'123','continue',{'f1':'No','f2':'Immediate'},r['form_version'])
        self.assertEqual(r['status'],'validation_error')
        self.assertTrue(await self.page.locator('input[type=radio]').nth(1).is_checked())
        self.assertIn('Invalid input',r['errors'])

    async def test_conditional_question_stops_batch_before_wrong_field(self):
        r=await self.form('''<label>First<input oninput="document.getElementById('conditional').innerHTML='<label>New<input></label>'"></label>
        <span id="conditional"></span><label>Second<input id="second"></label><button>Next</button>''')
        r=await self.adapter.execute(self.browser,'123','continue',{'f1':'yes','f2':'wrong'},r['form_version'])
        self.assertEqual(r['status'],'browser_required')
        self.assertEqual(await self.page.locator('#second').input_value(),'')

    async def test_country_options_are_compact_and_review_text_is_preserved(self):
        options=''.join(f'<option>Country{i}</option>' for i in range(250))
        r=await self.form(f'<label>Country<select>{options}</select></label><p>Cover letter: old company</p><button>Next</button>')
        self.assertEqual(r['fields'][0]['option_count'],250)
        self.assertNotIn('options',r['fields'][0])
        self.assertNotIn('Country249',r['context'])
        self.assertIn('old company',r['context'])

    async def test_browser_handoff_changes_revision_and_reuses_tab(self):
        r=await self.form('<label>Name<input></label><button>Next</button>')
        await self.page.locator('input').fill('Browser fix')
        again=await self.adapter.execute(self.browser,'123')
        self.assertEqual(r['tab_id'],again['tab_id'])
        self.assertNotEqual(r['form_version'],again['form_version'])
        self.assertEqual(again['fields'][0]['value'],'Browser fix')

    async def test_submit_confirmation_and_repeat_guard(self):
        r=await self.form('''<p>Review your application</p><button onclick="window.clicks=(window.clicks||0)+1;document.querySelector('[role=dialog]').innerHTML='<p>Application sent</p>'">Submit application</button>''')
        # Isolate evidence under the test root while exercising real durable receipts.
        with patch('easy_apply.ROOT',Path(self.tmp.name)), patch.object(self.page,'screenshot',new=AsyncMock(side_effect=AssertionError('No screenshots'))):
            r=await self.adapter.execute(self.browser,'123','submit',{},r['form_version'],str(Path(self.tmp.name)/'runs/test'))
        self.assertEqual(r['status'],'applied',r)
        proof=json.loads(Path(r['evidence']).read_text(encoding='utf-8'))
        self.assertEqual(proof['job_id'],'123')
        self.assertEqual(proof['confirmation_text'],'Application sent')
        self.assertEqual(proof['url'],self.page.url)
        self.assertTrue(proof['confirmed_at'])
        self.assertEqual(Path(r['before_submit']).suffix,'.json')
        self.assertFalse(list(Path(self.tmp.name).rglob('*.png')))
        again=await self.adapter.execute(self.browser,'123','submit',{},'anything','runs/test')
        self.assertEqual(again['status'],'applied')
        self.assertEqual(await self.page.evaluate('window.clicks'),1)

    async def test_unconfirmed_click_is_not_retried_after_restart(self):
        r=await self.form('<button onclick="window.clicks=(window.clicks||0)+1">Submit application</button>')
        with patch('easy_apply.ROOT',Path(self.tmp.name)), patch('easy_apply.CONFIRM_TIMEOUT',100):
            r=await self.adapter.execute(self.browser,'123','submit',{},r['form_version'],str(Path(self.tmp.name)/'runs/test'))
        self.assertEqual(r['status'],'submission_uncertain')
        restarted=EasyApply(self.tmp.name)
        r=await restarted.execute(self.browser,'123','submit',{},'old','runs/test')
        self.assertEqual(r['status'],'submission_uncertain')
        self.assertEqual(await self.page.evaluate('window.clicks'),1)

    async def test_uncertain_receipt_survives_restart(self):
        self.adapter.save('123',{'submission':'submission_uncertain','tab_id':'test'})
        other=EasyApply(self.tmp.name)
        r=await other.execute(self.browser,'123','submit',{},'old','runs/test')
        self.assertEqual(r['status'],'submission_uncertain')

    async def test_unknown_widget_stops_and_wrong_job_is_rejected(self):
        r=await self.form('<div contenteditable="true">Custom</div><button>Next</button>')
        self.assertEqual(r['status'],'browser_required')
        self.assertFalse(matches_job('https://evil.example/jobs/view/123/','123'))
        self.assertFalse(matches_job('https://www.linkedin.com/jobs/view/1234/','123'))
        await self.page.goto('https://www.linkedin.com/jobs/view/999/')
        r=await self.adapter.execute(self.browser,'123')
        self.assertEqual(r['status'],'browser_required')


if __name__ == '__main__':
    unittest.main()
