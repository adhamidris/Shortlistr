"""We Work Remotely RSS search over MCP stdio."""
import os
from mcp.server.fastmcp import FastMCP
from discovery_runtime import search_jobs as collect_jobs
from linkedin import ROOT

mcp = FastMCP('weworkremotely')


@mcp.tool()
async def search_jobs(output: str, keyword: str = '', since: str | None = None,
                      feeds: list[str] | None = None) -> str:
    """Collect full descriptions; return summary + results_file only.

    output: fresh runs/...json. keyword: title/description phrase, empty for all.
    since: ISO date/time, default past week. feeds: all/programming/other/sales-marketing;
    omitted uses all four. Snapshots are reused for 15m and are not a complete archive.
    Register even partial outputs; registration merges overlapping query IDs.
    """
    return await collect_jobs('weworkremotely', output, keyword, since, feeds=feeds)


if __name__ == '__main__':
    os.umask(0o077)
    os.chdir(ROOT)
    mcp.run(transport='stdio')
