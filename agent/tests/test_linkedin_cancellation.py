"""Cancel actual collection/MCP wrapper flow with an isolated mocked browser."""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import anyio
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import linkedin
import linkedin_mcp


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def check_cancel(self, stage, level_triggered):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as patches:
            root = Path(tmp)
            output, cache = root / 'jobs.json', root / 'cache.jsonl'
            entered = asyncio.Event()
            finished = []
            page = SimpleNamespace(url='https://www.linkedin.com/jobs/search/',
                                   set_default_timeout=Mock(), close=AsyncMock())
            browser = SimpleNamespace(contexts=[SimpleNamespace(new_page=AsyncMock(return_value=page))])
            pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser)),
                                 stop=AsyncMock())
            manager = SimpleNamespace(start=AsyncMock(return_value=pw))
            patches.enter_context(patch('playwright.async_api.async_playwright',return_value=manager))
            patches.enter_context(patch.object(linkedin,'ROOT',root))

            async def pause():
                entered.set()
                await asyncio.Event().wait()
                finished.append('unexpected continuation')

            async def search(page,args,collection,selected,summary):
                collection.add({'job_id':'123','title':'Test','company':'Test'})
                selected.append('123')
                summary.update(status='in_progress',new_jobs=1)
                if stage == 'search':
                    await pause()
                summary['status'] = 'complete'

            async def enrich(page,args,collection,selected,summary):
                summary['status'] = 'in_progress'
                collection.jobs['123']['description'] = 'Collected description'
                await pause()

            patches.enter_context(patch.object(linkedin,'search',search))
            patches.enter_context(patch.object(linkedin,'enrich_results',enrich))
            async def call():
                await linkedin_mcp.search_jobs('AI',location='Egypt',output=str(output),cache=str(cache))

            if level_triggered == 'timeout':
                with self.assertRaises(TimeoutError):
                    with anyio.fail_after(0.05):
                        await call()
            elif level_triggered:
                async with anyio.create_task_group() as group:
                    group.start_soon(call)
                    await entered.wait()
                    group.cancel_scope.cancel()
            else:
                task = asyncio.create_task(call())
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            data = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(data['summary']['status'],'interrupted')
            self.assertEqual(data['jobs'][0]['job_id'],'123')
            self.assertEqual(data['jobs'][0]['description_status'],
                             'complete' if stage == 'enrich' else 'incomplete')
            self.assertTrue(cache.exists())
            page.close.assert_awaited_once()
            pw.stop.assert_awaited_once()
            self.assertEqual(finished,[])
            with linkedin.scraper_lock():
                pass  # A follow-up call can acquire the lock immediately.

    async def test_asyncio_cancel_during_collection(self):
        await self.check_cancel('search',False)

    async def test_mcp_scope_cancel_during_collection(self):
        await self.check_cancel('search',True)

    async def test_mcp_scope_cancel_during_enrichment(self):
        await self.check_cancel('enrich',True)

    async def test_forced_timeout_stops_and_preserves_results(self):
        await self.check_cancel('enrich','timeout')
