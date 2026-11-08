"""Hackathon Docs MCP: OUR OWN read-only MCP server over Ramp's public API reference.

This is NOT a Ramp MCP server. Ramp's docs-only Developer MCP was retired
(see README). This server re-exposes the same public data (via
seller/ramp_docs.py, which fetches https://docs.ramp.com/llms-api.txt) as MCP
tools, so any MCP client (our seller agent, Claude Code, MCP Inspector) can use it.

Every tool is read-only, takes bounded inputs, and returns plain data.

Run:  .venv/bin/python -m docs_mcp.server        -> http://127.0.0.1:9102/mcp
Try:  claude mcp add --transport http hackathon-docs http://127.0.0.1:9102/mcp
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from seller import vendor_docs, vendor_guides
from seller.knowledge import _norm  # same normalisation the seller uses

HOST = os.environ.get("DOCS_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("DOCS_MCP_PORT", "9102"))
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                            openWorldHint=True)

mcp = MCPServer(
    name="hackathon-ramp-docs",
    title="Hackathon Docs MCP (unofficial, over Ramp's and Brex's public API docs)",
    version="0.2.0",
    instructions=("Read-only search over official public developer docs: Ramp (https://docs.ramp.com/llms-api.txt) "
                  "and Brex (public OpenAPI specs at developer.brex.com). Pass vendor='ramp' or 'brex'. "
                  "Operated by a hackathon team, not by either vendor. Results show what is documented, "
                  "not what has been tested."),
)


async def _snapshot(vendor: str):
    snap, err = await vendor_docs.get_snapshot(vendor)
    if snap is None:
        raise RuntimeError(f"{vendor} docs unavailable: {err}")
    return snap, err


def _meta(snap, err) -> dict[str, Any]:
    return {"vendor": snap.vendor, "source_url": snap.source_url, "retrieved_at": snap.retrieved_at,
            "from_cache": snap.from_cache, "refresh_error": err,
            "operated_by": "hackathon team (not the vendor)"}


def _op(o, full: bool = False) -> dict[str, Any]:
    d = o.as_evidence()
    if full:
        d["optional_request_fields"] = o.optional_fields
        d["description"] = o.description[:2000]
    return d


def _snippet(text: str, phrase: str, width: int = 120) -> str:
    i = text.lower().find(phrase.lower())
    if i < 0:
        return text[: 2 * width]
    return " ".join(text[max(0, i - width): i + len(phrase) + width].split())


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def docs_status(vendor: str = "ramp") -> dict[str, Any]:
    """Where a vendor's docs come from, when they were retrieved, and how many operations they contain."""
    snap, err = await _snapshot(vendor)
    return {**_meta(snap, err), "total_operations": len(snap.operations)}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def list_operations(path_prefix: str, limit: int = 50, vendor: str = "ramp") -> dict[str, Any]:
    """List documented API operations under a path prefix, e.g. '/developer/v1/vendors'."""
    snap, err = await _snapshot(vendor)
    prefix = path_prefix.strip()[:200].rstrip("/")
    limit = max(1, min(limit, 100))
    ops = [o for o in snap.operations if o.path == prefix or o.path.startswith(prefix + "/")]
    return {**_meta(snap, err), "match_count": len(ops), "operations": [_op(o) for o in ops[:limit]]}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def search_operations(phrase: str, limit: int = 20, vendor: str = "ramp",
                            in_description: bool = False, path_prefix: str = "") -> dict[str, Any]:
    """Find operations whose title (or, with in_description, title + description) contains a phrase,
    optionally only under an API path prefix (e.g. '/developer/v1/transactions')."""
    snap, err = await _snapshot(vendor)
    pref = path_prefix.strip()[:200].rstrip("/")
    p = _norm(phrase[:100]).strip()
    limit = max(1, min(limit, 50))
    def hay(o):
        return _norm(o.title + " " + o.description) if in_description else _norm(o.title)
    ops = [o for o in snap.operations if len(p) >= 4 and p in hay(o)
           and (not pref or o.path == pref or o.path.startswith(pref + "/"))]
    out = []
    for o in ops[:limit]:
        d = _op(o)
        if in_description:
            d["snippet"] = _snippet(o.description, phrase.strip()[:100])
        out.append(d)
    return {**_meta(snap, err), "match_count": len(ops), "operations": out}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_operation(title: str, vendor: str = "ramp") -> dict[str, Any]:
    """Full detail (required + optional request fields) for one operation, by exact title."""
    snap, err = await _snapshot(vendor)
    t = title.strip()[:200].lower()
    ops = [o for o in snap.operations if o.title.lower() == t]
    return {**_meta(snap, err), "found": bool(ops), "operations": [_op(o, full=True) for o in ops]}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_schema(name: str, vendor: str = "ramp") -> dict[str, Any]:
    """A documented schema's fields (e.g. Brex 'Money'), with types and descriptions."""
    snap, err = await _snapshot(vendor)
    sch = snap.schemas.get(name.strip()[:100])
    return {**_meta(snap, err), "found": sch is not None,
            "schema": {"name": sch.name, "fields": sch.fields, "description": sch.description,
                       "source_url": sch.source_url, "sources": sch.sources} if sch else None}


async def _guides(vendor: str):
    gs, err = await vendor_guides.get_guides(vendor)
    if gs is None:
        raise RuntimeError(f"{vendor} guides unavailable: {err}")
    return gs, err


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def guide_status(vendor: str = "ramp") -> dict[str, Any]:
    """Which official guide pages are indexed for a vendor (money units, scopes, pagination, webhooks)."""
    gs, err = await _guides(vendor)
    return {"vendor": vendor, "sources": gs.sources, "retrieved_at": gs.retrieved_at, "from_cache": gs.from_cache,
            "refresh_error": err, "passages": len(gs.passages), "operated_by": "hackathon team (not the vendor)"}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def search_guides(phrase: str, vendor: str = "ramp", limit: int = 10) -> dict[str, Any]:
    """Find passages in a vendor's official guides that contain ALL words of a phrase,
    e.g. 'smallest denomination' or 'transactions:read'. Returns the passage, snippet and source URL."""
    gs, err = await _guides(vendor)
    hits = vendor_guides.search(gs, phrase[:120], max(1, min(limit, 20)))
    return {"vendor": vendor, "retrieved_at": gs.retrieved_at, "from_cache": gs.from_cache, "refresh_error": err,
            "operated_by": "hackathon team (not the vendor)", "match_count": len(hits), "passages": hits}


if __name__ == "__main__":
    mcp.run("streamable-http", host=HOST, port=PORT)
