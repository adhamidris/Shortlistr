import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import discovery
from job_identity import same_posting,greenhouse_ref


class IdentityTests(unittest.TestCase):
    def test_cross_source_exact_greenhouse_and_not_generic_company(self):
        a={'url':'https://job-boards.greenhouse.io/acme/jobs/123?source=li'}
        self.assertTrue(same_posting(a,{'external_apply_url':'https://acme.com/careers/job?gh_jid=123'}))
        self.assertTrue(same_posting(a,{'url':'https://boards.greenhouse.io/embed/job_app?for=acme&token=123'}))
        self.assertFalse(same_posting(a,{'url':'https://job-boards.greenhouse.io/acme/jobs/124'}))
        self.assertFalse(same_posting({'url':'https://acme.com/careers'}, {'url':'https://acme.com/careers'}))
        with self.assertRaises(ValueError):greenhouse_ref('https://job-boards.greenhouse.io.evil.test/acme')


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_reuses_download_without_losing_unresolved_jobs(self):
        calls=[]
        now=datetime.now(timezone.utc).isoformat()
        def respond(request):
            calls.append(request.url)
            return httpx.Response(200,json={'job-count':1,'jobs':[dict(id=7,title='AI Engineer',company_name='Example',url='https://remotive.com/job/7',description='<p>Paid Python agents</p>',publication_date=now)]})
        with tempfile.TemporaryDirectory() as tmp, patch('discovery.asyncio.sleep',return_value=None):
            root=Path(tmp)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                for i in range(2):
                    result=await discovery.collect('remotive','agents',root/f'{i}.json',directory=root/'cache',client=client)
                    self.assertEqual(result['matched_jobs'],1)
                    self.assertEqual(result['status'],'complete')
                empty=await discovery.collect('remotive','aid',root/'unmatched.json',directory=root/'cache',client=client)
                self.assertEqual(empty['matched_jobs'],0)
                self.assertEqual(len(calls),1)
                with self.assertRaises(ValueError):
                    await discovery.collect('remotive','',root/'0.json',directory=root/'cache',client=client)

    async def test_cached_jobs_can_correctly_produce_zero_after_date_and_phrase_filters(self):
        rows=[dict(id=n,title=title,company_name='Example',url=f'https://remotive.com/job/{n}',
                   description='Description',publication_date=date) for n,title,date in [
            (1,'AI Native Developer','2026-11-13T12:00:00'),
            (2,'Content Reviewer','2026-11-19T12:00:00')]]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda req:httpx.Response(200,json={'job-count':2,'jobs':rows}))) as client:
                result=await discovery.collect('remotive','AI Native',root/'out.json',
                    since='2026-11-16T00:00:00Z',directory=root/'cache',client=client)
                self.assertEqual(result['matched_jobs'],0)
                self.assertEqual(result['filter_counts'],{'examined':2,'outside_date_window':1,'keyword_mismatch':1})
                result=await discovery.collect('remotive','AI Native',root/'older.json',
                    since='2026-11-01T00:00:00Z',directory=root/'cache',client=client)
                self.assertEqual(result['matched_jobs'],1)
                self.assertTrue(result['sources'][0]['cached'])

    async def test_partial_feed_failure_and_duplicate_items(self):
        xml='<rss><channel><item><title>A: AI Engineer</title><link>https://weworkremotely.com/job/a</link><guid>a</guid><description>Agents</description></item></channel></rss>'
        calls=[]
        def respond(request):
            calls.append(str(request.url))
            return httpx.Response(403 if 'other' in str(request.url) else 200,text=xml)
        with tempfile.TemporaryDirectory() as tmp,patch('discovery.asyncio.sleep',return_value=None):
            root=Path(tmp)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                r=await discovery.collect('weworkremotely','',root/'out.json',feeds=['all','programming','other'],directory=root/'cache',client=client)
                self.assertEqual(r['status'],'partial');self.assertEqual(r['matched_jobs'],1)
                self.assertEqual(len(json.loads((root/'out.json').read_text(encoding='utf-8'))['jobs']),1)

    async def test_rate_limit_persists_across_calls(self):
        calls=[]
        def respond(request):
            calls.append(1);return httpx.Response(429,headers={'Retry-After':'180'})
        with tempfile.TemporaryDirectory() as tmp,patch('discovery.asyncio.sleep',return_value=None):
            root=Path(tmp)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                for i in range(2):
                    r=await discovery.collect('remotive','',root/f'{i}.json',directory=root/'cache',client=client)
                    self.assertEqual(r['status'],'error')
                self.assertEqual(len(calls),1)

    async def test_cancel_saves_interrupted_result(self):
        async def cancelled(request):raise asyncio.CancelledError()
        with tempfile.TemporaryDirectory() as tmp,patch('discovery.asyncio.sleep',return_value=None):
            root=Path(tmp)
            async with httpx.AsyncClient(transport=httpx.MockTransport(cancelled)) as client:
                with self.assertRaises(asyncio.CancelledError):
                    await discovery.collect('remotive','',root/'out.json',directory=root/'cache',client=client)
                self.assertEqual(json.loads((root/'out.json').read_text(encoding='utf-8'))['summary']['status'],'interrupted')

if __name__=='__main__':unittest.main()
