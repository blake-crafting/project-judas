"""One-time Airtable OAuth login for Judas. Opens Airtable's approval page in the browser; after you
approve, the login is stored in data/oauth/airtable.json (git-ignored) and the MCP tools are listed."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vendors_live.mcp_live import vendor_session  # noqa: E402


async def main() -> None:
    async with vendor_session("airtable", interactive=True, timeout=60) as c:
        tools = (await c.list_tools()).tools
    print(f"Airtable login OK: {len(tools)} MCP tools available", flush=True)


asyncio.run(main())
