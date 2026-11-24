"""MCP boundary checks without LinkedIn requests."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import linkedin_mcp


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_reuses_scraper_and_returns_only_summary(self):
        summary={'status':'complete','results_file':'runs/test/jobs.json','new_jobs':2}
        stdout=io.StringIO()
        with patch.object(linkedin_mcp.linkedin,'run',new=AsyncMock(return_value=summary)) as run:
            with patch.object(linkedin_mcp.linkedin,'scraper_lock',contextlib.nullcontext), contextlib.redirect_stdout(stdout):
                result=await linkedin_mcp.search_jobs('AI Engineer',location='Egypt',output='runs/test/jobs.json',posted_within='24h',cache='runs/test/cache.jsonl')
        self.assertEqual(json.loads(result),summary)
        self.assertEqual(stdout.getvalue(),'')
        self.assertFalse(run.call_args.kwargs['emit'])
        args=run.call_args.args[0]
        self.assertEqual(args.posted_within,'24h')
        self.assertEqual(str(args.cache),'runs/test/cache.jsonl')

    async def test_stdio_catalog_and_invalid_input(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        params=StdioServerParameters(command=sys.executable,args=[str(Path(linkedin_mcp.__file__))])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                catalog=await session.list_tools()
                self.assertEqual({t.name for t in catalog.tools},{'search_jobs','easy_apply'})
                search=next(t for t in catalog.tools if t.name=='search_jobs')
                self.assertIn('location',search.inputSchema['required'])
                result=await session.call_tool('search_jobs',{'keyword':'AI','location':'Egypt','max_pages':0})
                self.assertEqual(json.loads(result.content[0].text)['status'],'error')
