"""Greenhouse employer boards and hosted applications over MCP stdio."""
import os
from mcp.server.fastmcp import FastMCP
from discovery_runtime import search_jobs as collect_jobs
from linkedin import ROOT

from discovery_runtime import apply_job

mcp = FastMCP('greenhouse')
mcp.tool()(apply_job)


@mcp.tool()
async def search_jobs(board_url: str, output: str, keyword: str = '',
                      since: str | None = None) -> str:
    """Collect a public Greenhouse employer board with full job descriptions.

    board_url is a verified public Greenhouse board/job URL; choose employers dynamically.
    output: fresh runs/...json. keyword filters title/description, empty means all.
    since: ISO date/time, default past week. Responses cached 15m. Returns summary
    and results_file only; register every output, including partial results.
    """
    return await collect_jobs('greenhouse', output, keyword, since, board_url=board_url)


if __name__ == '__main__':
    os.umask(0o077)
    os.chdir(ROOT)
    mcp.run(transport='stdio')
