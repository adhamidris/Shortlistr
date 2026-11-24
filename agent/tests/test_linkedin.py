"""Offline behavior checks. No requests to LinkedIn and no application actions."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))

SPEC = importlib.util.spec_from_file_location('linkedin', Path(__file__).parents[1] / 'scripts/linkedin.py')
li = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(li)


class CollectionTests(unittest.TestCase):
    def test_unicode_separators_in_description_survive_reload(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'jobs.jsonl'
            description = 'First\u2028second\u2029third\x85last'
            collection = li.Collection(path)
            collection.add({'job_id': '123', 'description': description})
            collection.add({'job_id': '456', 'description': 'Another job'})
            collection.save()
            restored = li.Collection(path)
            self.assertEqual(set(restored.jobs), {'123', '456'})
            self.assertEqual(restored.jobs['123']['description'], description)

    def test_external_destination_decodes_only_safe_external_urls(self):
        url = 'https://jobs.ashbyhq.com/The-Flex/123?source=LinkedIn'
        from urllib.parse import urlencode
        wrapped = 'https://www.linkedin.com/safety/go/?' + urlencode({'url':url})
        self.assertEqual(li.external_destination(wrapped), url)
        self.assertEqual(li.external_destination(url), url)
        for invalid in [None, '/jobs/view/123/', 'javascript:alert(1)',
                        'https://www.linkedin.com/jobs/view/123/',
                        'https://www.linkedin.com/safety/go/?url=javascript%3Aalert(1)']:
            self.assertIsNone(li.external_destination(invalid))
    def test_dedupe_persists_and_does_not_discard_cached_description(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'jobs.jsonl'
            c = li.Collection(path)
            self.assertTrue(c.add({'job_id':'123', 'title':'AI Engineer'}))
            c.jobs['123']['description'] = 'Full description'
            c.save()
            c = li.Collection(path)
            self.assertFalse(c.add({'job_id':'123', 'title':'AI Engineer', 'description':None}))
            self.assertEqual(c.jobs['123']['description'], 'Full description')

    def test_corrupt_collection_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'jobs.jsonl'
            path.write_text('not json\n', encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):
                li.Collection(path)
            self.assertEqual(path.read_text(encoding='utf-8'), 'not json\n')

    def test_details_before_search_does_not_hide_new_card(self):
        with tempfile.TemporaryDirectory() as d:
            c = li.Collection(Path(d) / 'jobs.jsonl')
            c.add({'job_id':'123', 'description':'Cached description'})
            self.assertTrue(c.add({'job_id':'123', 'title':'Engineer', 'company':'Acme'}))
            self.assertEqual(c.jobs['123']['description'], 'Cached description')
            self.assertFalse(c.add({'job_id':'123', 'title':'Engineer', 'company':'Acme'}))

    def test_filters_are_explicit_url_parameters(self):
        q = parse_qs(urlparse(li.search_url('AI Engineer', 'Egypt', 'week', True)).query)
        self.assertEqual(q['keywords'], ['AI Engineer'])
        self.assertEqual(q['geoId'], ['106155005'])
        self.assertEqual(q['f_TPR'], ['r604800'])
        self.assertEqual(q['f_WT'], ['2'])
        self.assertNotIn('f_TPR', parse_qs(urlparse(li.search_url('AI', 'Egypt', 'any')).query))

    def test_all_supported_countries_keep_real_geo_filters(self):
        import linkedin_mcp
        from typing import get_type_hints
        self.assertIs(get_type_hints(linkedin_mcp.search_jobs)['location'], str)
        for country, geo in li.GEO_IDS.items():
            for remote in (False, True):
                with self.subTest(country=country, remote=remote):
                    args = li.parse_args(['search', '--keyword', 'AI Engineer', '--location', country])
                    self.assertEqual(args.location, country)
                    q = parse_qs(urlparse(li.search_url('AI Engineer', country, 'week', remote)).query)
                    self.assertEqual(q['geoId'], [geo])
                    self.assertEqual(q['keywords'], ['AI Engineer'])
                    self.assertEqual(q.get('f_WT'), ['2'] if remote else None)

    def test_unknown_country_does_not_degrade_to_keyword_search(self):
        with self.assertRaises(KeyError):
            li.search_url('AI Engineer', 'Unknown country', 'week')



class SearchScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfirmed_search_scope_stops_before_collection(self):
        from unittest.mock import AsyncMock, Mock
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        page = Mock()
        page.get_by_role.return_value.wait_for = AsyncMock(side_effect=PlaywrightTimeout('wrong scope'))
        with patch.object(li, 'check_access', AsyncMock()):
            with self.assertRaises(li.ScrapeError) as error:
                await li.verify_search_scope(page, 'Germany', False)
        self.assertEqual(error.exception.status, 'filter_not_applied')


class BrowserFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='chrome', headless=True)
        # Dedicated fixture browser: no shared profile, cookies or live tabs.
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.pw.stop()

    async def test_country_and_workplace_scope_are_verified_in_ui(self):
        for country in ('Germany', 'Netherlands', 'Ireland'):
            for remote in (False, True):
                label = 'Remote' if remote else 'Workplace type'
                checked = 'checked' if remote else ''
                await self.page.set_content(f'<button aria-label="Location {country}">{country}</button>'
                                            f'<label><input type="checkbox" {checked}>{label}</label>')
                await li.verify_search_scope(self.page, country, remote)

    async def test_apply_link_extraction_ignores_description_links_and_easy_apply(self):
        await self.page.set_content('''<main>
          <a href="https://company.example">Company website</a>
          <a aria-label="Apply on company website"
             href="https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fjobs.ashbyhq.com%2Frole">Apply</a>
          </main>''')
        self.assertEqual(await li.extract_external_apply_url(self.page), 'https://jobs.ashbyhq.com/role')
        await self.page.set_content('<main><button>Easy Apply</button><a href="https://company.example">Company website</a></main>')
        self.assertIsNone(await li.extract_external_apply_url(self.page))
        await self.page.set_content('<main><a href="https://a.example">Apply</a><a href="https://b.example">Apply</a></main>')
        self.assertIsNone(await li.extract_external_apply_url(self.page))

    async def test_nested_card_wrappers_and_duplicate_title(self):
        await self.page.set_content('''<div componentkey="job-card-component-ref-123">
          <div componentkey="job-card-component-ref-123" role="button">
          <p>AI Engineer<br>AI Engineer</p><p>Acme</p><p>Cairo (Remote)</p>
          <p>Posted 2 hours ago</p><p>Easy Apply</p></div></div>''')
        jobs = await self.page.evaluate(li.CARDS_JS)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['title'], 'AI Engineer')
        self.assertEqual(jobs[0]['company'], 'Acme')
        self.assertEqual(jobs[0]['job_id'], '123')

    async def test_legacy_skip_is_returned_from_cache_without_new_detail_visit(self):
        import assessment_policy
        await self.page.set_content('<div></div>')
        with tempfile.TemporaryDirectory() as d:
            args=li.parse_args(['search','--location','Egypt','--keyword','Python','--posted-within','any',
                               '--cache',d+'/cache.jsonl','--output',d+'/out.json'])
            c=li.Collection(args.cache)
            c.add({'job_id':'111','title':'Python Developer','company':'Acme','description':'Saved description'})
            selected=[];summary={'pages_scanned':0,'already_collected':0,'fetched_descriptions':0}
            async def noop(*a):pass
            async def collect(page):return [{'job_id':'111','title':'Python Developer','company':'Acme'}],True
            async def unexpected(*a):raise AssertionError('Cached JD must not be fetched again')
            with patch.object(assessment_policy,'recheck_ids',return_value={'111'}), patch.object(li,'goto',noop), patch.object(li,'results_ready',noop), patch.object(li,'verify_search_scope',noop), patch.object(li,'collect_page',collect), patch.object(li,'fetch_description',unexpected):
                await li.search(self.page,args,c,selected,summary)
                await li.enrich_results(self.page,args,c,selected,summary)
            self.assertEqual(selected,['111'])
            self.assertEqual(summary['reconsidered_jobs'],1)
            self.assertEqual(summary['fetched_descriptions'],0)

    async def test_known_first_page_does_not_stop_pagination(self):
        await self.page.set_content('''<div id="cards"></div>
          <button aria-label="Page 2" onclick="document.getElementById('cards').innerHTML=
          '<div componentkey=job-card-component-ref-222></div>'; this.remove()">2</button>''')
        with tempfile.TemporaryDirectory() as d:
            args = li.parse_args(['search','--location','Egypt','--keyword','AI','--posted-within','any',
                                 '--cache',d+'/cache.jsonl','--output',d+'/out.json'])
            c = li.Collection(args.cache)
            c.add({'job_id':'111', 'title':'Known', 'company':'Acme', 'description':'Already fetched'})
            selected = []
            summary = {'pages_scanned':0, 'already_collected':0}
            async def noop(*a): pass
            async def collect(page):
                key = '111' if await page.get_by_role('button',name='Page 2').count() else '222'
                return [{'job_id':key,'title':'AI Engineer','company':'Acme'}], True
            with patch.object(li,'goto',noop), patch.object(li,'results_ready',noop), patch.object(li,'verify_search_scope',noop), patch.object(li,'collect_page',collect):
                await li.search(self.page,args,c,selected,summary)
            self.assertEqual(selected, ['222'])
            self.assertEqual(summary['pages_scanned'], 2)
            self.assertEqual(summary['status'], 'complete')

    async def test_bad_filter_stops_before_collecting(self):
        await self.page.set_content('<div></div>')
        with tempfile.TemporaryDirectory() as d:
            args = li.parse_args(['search','--location','Egypt','--keyword','AI','--cache',d+'/cache.jsonl'])
            async def noop(*a): pass
            with patch.object(li,'goto',noop), patch.object(li,'results_ready',noop), patch.object(li,'verify_search_scope',noop):
                with self.assertRaises(li.ScrapeError) as error:
                    await li.search(self.page,args,li.Collection(args.cache),[],{})
            self.assertEqual(error.exception.status, 'filter_not_applied')

    async def test_page_cap_preserves_results_and_reports_partial(self):
        await self.page.set_content('<button aria-label="Page 2">2</button>')
        with tempfile.TemporaryDirectory() as d:
            args = li.parse_args(['search','--location','Egypt','--keyword','AI','--posted-within','any',
                                 '--max-pages','1','--cache',d+'/cache.jsonl','--output',d+'/out.json'])
            c, selected = li.Collection(args.cache), []
            summary = {'pages_scanned':0, 'already_collected':0}
            async def noop(*a): pass
            async def collect(page):
                return [{'job_id':'111','title':'Engineer','company':'Acme'}], True
            with patch.object(li,'goto',noop), patch.object(li,'results_ready',noop), patch.object(li,'verify_search_scope',noop), patch.object(li,'collect_page',collect):
                await li.search(self.page,args,c,selected,summary)
            li.checkpoint(args,c,selected,summary)
            result = json.loads(args.output.read_text(encoding='utf-8'))
            self.assertEqual(result['summary']['status'], 'partial')
            self.assertEqual(result['jobs'][0]['job_id'], '111')
            self.assertIn('111', li.Collection(args.cache).jobs)

    async def test_search_returns_descriptions_and_repeat_skips_visits(self):
        await self.page.set_content('<div></div>')
        with tempfile.TemporaryDirectory() as d:
            args = li.parse_args(['search','--location','Egypt','--keyword','AI','--posted-within','any',
                                 '--cache',d+'/cache.jsonl','--output',d+'/out.json'])
            calls = []
            async def noop(*a): pass
            async def collect(page):
                return [{'job_id':'111','title':'Engineer','company':'Acme'}], True
            async def fetch(page, key):
                calls.append(key)
                return 'Full job description'
            for attempt in range(2):
                c, selected = li.Collection(args.cache), []
                summary = {'pages_scanned':0, 'already_collected':0, 'fetched_descriptions':0}
                with patch.object(li,'goto',noop), patch.object(li,'results_ready',noop), patch.object(li,'verify_search_scope',noop), patch.object(li,'collect_page',collect), patch.object(li,'fetch_description',fetch):
                    await li.search(self.page,args,c,selected,summary)
                    await li.enrich_results(self.page,args,c,selected,summary)
                li.checkpoint(args,c,selected,summary)
                result = json.loads(args.output.read_text(encoding='utf-8'))
                self.assertEqual(summary['status'], 'complete')
                if attempt == 0:
                    self.assertEqual(result['jobs'][0]['description'], 'Full job description')
                    self.assertEqual(result['jobs'][0]['description_status'], 'complete')
                else:
                    self.assertEqual(result['jobs'], [])
                    self.assertEqual(summary['already_collected'], 1)
            self.assertEqual(calls, ['111'])

    async def test_failed_description_is_flagged_and_retried_next_search(self):
        await self.page.set_content('<div></div>')
        with tempfile.TemporaryDirectory() as d:
            args = li.parse_args(['search','--location','Egypt','--keyword','AI','--posted-within','any',
                                 '--cache',d+'/cache.jsonl','--output',d+'/out.json'])
            async def noop(*a): pass
            async def collect(page):
                return [{'job_id':'111','title':'Engineer','company':'Acme'}], True
            async def fetch(page, key):
                if attempt == 0:
                    raise li.ScrapeError('description_unavailable', 'Unavailable')
                return 'Recovered description'
            for attempt in range(2):
                c, selected = li.Collection(args.cache), []
                summary = {'pages_scanned':0, 'already_collected':0, 'fetched_descriptions':0}
                with patch.object(li,'goto',noop), patch.object(li,'results_ready',noop), patch.object(li,'verify_search_scope',noop), patch.object(li,'collect_page',collect), patch.object(li,'fetch_description',fetch):
                    await li.search(self.page,args,c,selected,summary)
                    await li.enrich_results(self.page,args,c,selected,summary)
                li.checkpoint(args,c,selected,summary)
                result = json.loads(args.output.read_text(encoding='utf-8'))
                self.assertEqual(selected, ['111'])
                if attempt == 0:
                    self.assertEqual(summary['status'], 'partial')
                    self.assertEqual(result['jobs'][0]['description_status'], 'incomplete')
                    self.assertIn('description_error', result['jobs'][0])
                else:
                    self.assertEqual(summary['status'], 'complete')
                    self.assertEqual(summary['retried_jobs'], 1)
                    self.assertNotIn('description_error', result['jobs'][0])

    async def test_security_check_stops_remaining_description_visits(self):
        with tempfile.TemporaryDirectory() as d:
            args = li.parse_args(['search','--location','Egypt','--keyword','AI','--cache',d+'/cache.jsonl','--output',d+'/out.json'])
            c = li.Collection(args.cache)
            for key in ['111', '222']:
                c.add({'job_id':key})
            calls = []
            async def fetch(page, key):
                calls.append(key)
                raise li.ScrapeError('challenge', 'Security check')
            with patch.object(li,'fetch_description',fetch):
                with self.assertRaises(li.ScrapeError):
                    await li.enrich_results(self.page,args,c,['111','222'],{'status':'complete','fetched_descriptions':0})
            self.assertEqual(calls, ['111'])
            self.assertEqual(json.loads(args.output.read_text(encoding='utf-8'))['jobs'][1]['description_status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
