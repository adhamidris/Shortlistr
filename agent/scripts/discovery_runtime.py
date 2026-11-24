#!/usr/bin/env python3
"""Shared call execution, locking and deadlines for the job-source MCPs."""
from runtime import browser_endpoint
import asyncio
import json
from pathlib import Path
from typing import Literal

import httpx

import discovery
import greenhouse_apply
from linkedin import ROOT, playwright_client, scraper_lock



async def search_jobs(source: Literal['remotive','weworkremotely','greenhouse'], output: str,
                      keyword: str = '', since: str | None = None,
                      board_url: str | None = None, feeds: list[str] | None = None) -> str:
    """Collect full descriptions; return compact summary + file, not the job payload.

    output is a fresh runs/...json path. since is ISO date/time (default past week).
    keyword matches title/description, empty collects all. Greenhouse requires a
    public board_url; employers are chosen by the agent, not a fixed company list.
    WWR feeds: all/programming/other/sales-marketing; omitted uses all four.
    Remotive caches 6h, WWR/Greenhouse 15m; sequential keywords reuse downloads.
    Register every result file, including partial results. Registration merges
    duplicate IDs; ledger decisions distinguish resolved from unseen jobs.
    Feed coverage is finite; absence is not proof that no matching jobs exist.
    """
    try:
        path=Path(output).expanduser().resolve()
        if not path.is_relative_to(ROOT/'runs') or path.suffix!='.json':
            raise ValueError('Output must be a JSON file under runs/.')
        with scraper_lock():
            async with asyncio.timeout(7100):
                async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
                    result=await discovery.collect(source,keyword,path,since,board_url,feeds,client=client)
        return json.dumps(result,ensure_ascii=False)
    except TimeoutError:
        return json.dumps({'status':'interrupted','reason':'Operation stopped before MCP deadline. Register any saved partial output.','results_file':output})
    except Exception as exc:
        return json.dumps({'status':'error','reason':str(exc).split('\n')[0]})


async def apply_job(url: str, action: Literal['inspect','fill','submit']='inspect',
                    answers: dict[str,str|bool|list[str]] | None=None,
                    form_version: str | None=None) -> str:
    """Inspect/resume a Greenhouse hosted job application in the shared Chrome.

    inspect returns question IDs, required flags, choices, current values, context,
    tab_id and form_version. fill requires that version and an answers mapping;
    text/choice labels are strings, multi-selects lists, checkboxes booleans, file
    fields repo-local file paths. Review all prefilled values and special instructions.
    submit requires the fresh version, no answers; clicks once, stores evidence.
    browser_required: use Playwright on this tab, then inspect. submission_uncertain:
    verify outcome manually; retries never resubmit. No browser/profile copies.
    """
    try:
        with scraper_lock():
            async with asyncio.timeout(7100):
                async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
                    async with playwright_client() as pw:
                        browser=await pw.chromium.connect_over_cdp(browser_endpoint(),timeout=15000)
                        result=await greenhouse_apply.apply(browser.contexts[0],client,url,action,answers,form_version)
                        return json.dumps(result,ensure_ascii=False)
    except TimeoutError:
        return json.dumps({'status':'interrupted','reason':'Operation stopped; inspect the saved application before any further action.'})
    except Exception as exc:
        return json.dumps({'status':'error','reason':str(exc).split('\n')[0]})
