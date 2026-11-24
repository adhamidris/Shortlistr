import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import httpx
from playwright.async_api import async_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from greenhouse_apply import apply

URL='https://job-boards.greenhouse.io/example/jobs/123'
SCHEMA={'questions':[{'label':'First name','required':True,'fields':[{'name':'first_name','type':'input_text'}]},
                    {'label':'Experience','required':True,'fields':[{'name':'question_1','type':'textarea'}]}]}
HTML='''<form onsubmit="event.preventDefault();location.href='/example/jobs/123/confirmation'">
<label for="first_name">First name*</label><input id="first_name">
<label for="question_1">Experience*</label><textarea id="question_1"></textarea>
<button>Submit application</button></form>'''

class GreenhouseTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_stale_revision_confirmation_and_duplicate_protection(self):
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='chrome',headless=True)
            ctx=await browser.new_context();sent=[]
            async def route(r):
                if r.request.url.endswith('/confirmation'):
                    sent.append(1);body='<h1>Thank you for applying!</h1>'
                else:body=HTML
                await r.fulfill(content_type='text/html',body=body)
            await ctx.route('https://job-boards.greenhouse.io/**',route)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=SCHEMA))) as client:
                with tempfile.TemporaryDirectory() as tmp,patch('discovery.asyncio.sleep',return_value=None):
                    async def call(action,answers=None,rev=None):
                        return await apply(ctx,client,URL,action,answers,rev,Path(tmp)/'state',Path(tmp)/'cache')
                    s=await call('inspect');self.assertEqual(s['status'],'needs_answers')
                    self.assertEqual((await call('fill',{'first_name':'Candidate'},'stale'))['status'],'form_changed')
                    s=await call('fill',{'first_name':'Candidate','question_1':'My experience'},s['form_version'])
                    self.assertEqual(s['status'],'ready_to_submit')
                    result=await call('submit',rev=s['form_version'])
                    self.assertEqual(result['status'],'submitted');self.assertEqual(len(sent),1)
                    self.assertEqual((await call('submit',rev=s['form_version']))['status'],'submitted')
                    self.assertEqual(len(sent),1);self.assertEqual(len(ctx.pages),0)
            await browser.close()

    async def test_upload_widget_replacement_is_verified(self):
        schema={'questions':[{'label':'First name','required':True,'fields':[{'name':'first_name','type':'input_text'}]},
                             {'label':'CV','required':True,'fields':[{'name':'resume','type':'input_file'}]}]}
        html="""<form><label for="first_name">First name*</label><input id="first_name">
        <div class="file-upload"><label for="resume">CV*</label><input type="file" id="resume"
        onchange="this.parentElement.innerHTML='<label for=resume>CV*</label><span class=file-upload__filename>'+this.files[0].name+'</span>'"></div>
        <button>Submit application</button></form>"""
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='chrome',headless=True)
            ctx=await browser.new_context()
            await ctx.route('https://job-boards.greenhouse.io/**',lambda r:r.fulfill(content_type='text/html',body=html))
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=schema))) as client:
                with tempfile.TemporaryDirectory() as tmp,patch('greenhouse_apply.ROOT',Path(tmp).resolve()),patch('discovery.asyncio.sleep',return_value=None):
                    cv=Path(tmp)/'cv.txt';cv.write_text('Test fixture CV', encoding='utf-8')
                    s=await apply(ctx,client,URL,'inspect',directory=Path(tmp)/'state',cache=Path(tmp)/'cache')
                    s=await apply(ctx,client,URL,'fill',{'first_name':'Candidate','resume':str(cv)},s['form_version'],Path(tmp)/'state',Path(tmp)/'cache')
                    self.assertEqual(s['status'],'ready_to_submit',s.get('reason'))
                    self.assertEqual(next(f['value'] for f in s['fields'] if f['id']=='resume'),['cv.txt'])
            await browser.close()

    async def test_uncertain_submission_never_reclicks(self):
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='chrome',headless=True)
            ctx=await browser.new_context()
            await ctx.route('https://job-boards.greenhouse.io/**',lambda r:r.fulfill(content_type='text/html',body=HTML.replace("location.href='/example/jobs/123/confirmation'","window.clicks=(window.clicks||0)+1")))
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=SCHEMA))) as client:
                with tempfile.TemporaryDirectory() as tmp,patch('greenhouse_apply.asyncio.sleep',return_value=None):
                    async def call(action,answers=None,rev=None):return await apply(ctx,client,URL,action,answers,rev,Path(tmp)/'state',Path(tmp)/'cache')
                    s=await call('inspect');s=await call('fill',{'first_name':'Candidate','question_1':'Experience'},s['form_version'])
                    self.assertEqual((await call('submit',rev=s['form_version']))['status'],'submission_uncertain')
                    self.assertEqual((await call('submit',rev=s['form_version']))['status'],'submission_uncertain')
                    self.assertEqual(await ctx.pages[0].evaluate('window.clicks'),1)
            await browser.close()

if __name__=='__main__':unittest.main()
