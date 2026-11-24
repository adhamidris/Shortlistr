"""Remotive public job search over MCP stdio."""
import os
from mcp.server.fastmcp import FastMCP
from discovery_runtime import search_jobs as collect_jobs
from linkedin import ROOT

mcp = FastMCP('remotive')


@mcp.tool()
async def search_jobs(output: str, keyword: str = '', since: str | None = None) -> str:
    """Collect full descriptions; return summary + results_file only.

    output: fresh runs/...json. keyword: title/description phrase, empty for all.
    since: ISO date/time, default past week. Downloads are reused for six hours;
    public listings are delayed 24h. Register even partial outputs before assessment.
    Collection is not a decision; the ledger determines prior outcomes.
    """
    return await collect_jobs('remotive', output, keyword, since)


if __name__ == '__main__':
    os.umask(0o077)
    os.chdir(ROOT)
    mcp.run(transport='stdio')
