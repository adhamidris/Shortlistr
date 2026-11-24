#!/usr/bin/env python3
"""LinkedIn MCP over local stdio; uses the existing authenticated Chrome."""
import json
import os
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

import linkedin
from easy_apply import EasyApply, ENDPOINT

mcp = FastMCP('linkedin')


@mcp.tool()
async def search_jobs(keyword: str, location: str, output: str | None = None,
                      posted_within: Literal['24h','week','month','any'] = 'week',
                      remote_only: bool = False, max_pages: int | None = None,
                      cache: str | None = None) -> str:
    """Collect new LinkedIn jobs with full descriptions. Returns summary + file path only.

    Read queries/filters from targets.yaml. Location must be a configured verified LinkedIn geography. Deduplication is automatic; cache override
    is for isolated tests. Default all pages; cap only when requested. Register the
    results_file with run_report.py, including partial results. Does not apply.
    """
    argv = ['search','--keyword',keyword,'--location',location,'--posted-within',posted_within]
    for flag,value in [('--output',output),('--cache',cache),('--max-pages',max_pages)]:
        if value is not None:
            argv += [flag,str(value)]
    if remote_only:
        argv += ['--remote-only']
    try:
        args = linkedin.parse_args(argv)
    except SystemExit:
        return json.dumps({'status':'error','reason':'Invalid search arguments; check paths and max_pages.'})
    try:
        with linkedin.scraper_lock():
            return json.dumps(await linkedin.run(args,emit=False),ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'status':'error','reason':str(exc)})


@mcp.tool()
async def easy_apply(job_id: str, action: Literal['inspect','continue','submit'] = 'inspect',
                     answers: dict[str, str | bool | list[str]] | None = None,
                     form_version: str | None = None, run_dir: str | None = None) -> str:
    """Open/resume LinkedIn Easy Apply in the shared browser. External forms use Playwright.

    inspect returns fields, prefilled values, context, tab_id and form_version.
    continue requires that version: answers maps field IDs to strings, checkbox
    booleans, or choice labels/keys (lists for checkbox groups). Selects take labels.
    Optional upload_resume answer takes a repo-local PDF/DOC/DOCX path under 2 MB.
    Continue accepts existing values as reviewed, fills a batch and advances one
    page; never submits. Read each new page, including prefilled prose.
    Use Playwright on the returned tab for unsupported controls, then inspect.
    submit requires unchanged form_version and run_dir for evidence, no answers.
    It clicks final submission once. Record applied only with confirmed evidence;
    submission_uncertain requires browser inspection, never a blind retry.
    """
    try:
        with linkedin.scraper_lock():
            async with linkedin.playwright_client() as pw:
                browser = await pw.chromium.connect_over_cdp(ENDPOINT,timeout=15000)
                result = await EasyApply().execute(browser,job_id,action,answers,form_version,run_dir)
                # Stopping the Playwright client disconnects; do not close shared Chrome.
                return json.dumps(result,ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'status':'error','reason':str(exc).split('\n')[0]})


if __name__ == '__main__':
    os.umask(0o077)
    os.chdir(linkedin.ROOT)
    mcp.run(transport='stdio')
