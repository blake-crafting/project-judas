"""Sessions with the vendors' OFFICIAL hosted MCP servers (vendor-operated, not ours)."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from vendors_live.config import credential

ENDPOINTS = {"airtable": "https://mcp.airtable.com/mcp", "monday": "https://mcp.monday.com/mcp"}


class NotConfigured(RuntimeError):
    pass


def _usable_token(vendor: str) -> str | None:
    """A static credential, if it looks complete (an Airtable token has a '.'; a bare token id does not)."""
    secret = credential(vendor)
    if vendor == "airtable" and secret and "." not in secret:
        return None
    return secret


@asynccontextmanager
async def vendor_session(vendor: str, timeout: float = 30.0, interactive: bool = False):
    from vendors_live import oauth
    secret = _usable_token(vendor)
    if secret:
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {secret}"}, timeout=timeout)
    elif vendor in oauth.MCP_URL and (interactive or oauth.has_login(vendor)):
        if oauth.has_login(vendor):
            await oauth.ensure_fresh(vendor)        # renew an expired login first (it expires hourly)
        http = httpx2.AsyncClient(auth=oauth.provider(vendor, interactive=interactive), timeout=timeout)
    else:
        raise NotConfigured(f"{vendor} is not configured for live calls")
    try:
        async with Client(streamable_http_client(ENDPOINTS[vendor], http_client=http)) as client:
            yield client
    finally:
        await http.aclose()


def tool_record(t) -> dict:
    ann = t.annotations
    return {"name": t.name, "description": (t.description or "")[:400],
            "read_only": bool(ann and ann.read_only_hint), "destructive": bool(ann and ann.destructive_hint),
            "input_schema": t.input_schema if hasattr(t, "input_schema") else getattr(t, "inputSchema", None)}


async def timed_call(client, name: str, args: dict) -> tuple[dict | None, bool, int, str]:
    t0 = time.perf_counter()
    r = await client.call_tool(name, args)
    ms = round((time.perf_counter() - t0) * 1000)
    text = " ".join(getattr(c, "text", "") for c in (r.content or []))[:4000]
    return r.structured_content, bool(r.is_error), ms, text
