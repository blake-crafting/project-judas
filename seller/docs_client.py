"""Seller-side MCP client: gathers evidence by calling tools on our docs MCP server.

Guardrails:
- Connects only to the configured DOCS_MCP_URL (operator config, never buyer input).
- Calls only tools on TOOL_ALLOWLIST, and only after confirming the server
  advertises them as read-only.
- Tool output is DATA: parsed into Operation records; never executed or obeyed.
- Every call is logged (tool, args, match_count, ms) for the interaction trace.
"""

from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import urlparse

from mcp import Client

from seller import knowledge
from seller.ramp_docs import DocsSnapshot, Operation

DOCS_MCP_URL = os.environ.get("DOCS_MCP_URL", "http://127.0.0.1:9102/mcp")
TOOL_ALLOWLIST = {"docs_status", "list_operations", "search_operations", "get_operation", "get_schema",
                  "guide_status", "search_guides"}
TIMEOUT_S = 30


class McpEvidenceError(RuntimeError):
    pass


def _op_from(d: dict) -> Operation:
    return Operation(
        title=str(d.get("title", ""))[:200],
        path=str(d.get("path", ""))[:200],
        description=str(d.get("description", ""))[:2000],
        required_fields=[str(f)[:80] for f in d.get("required_request_fields", [])][:30],
    )


async def _call(client: Client, log: list[dict], tool: str, args: dict) -> dict:
    if tool not in TOOL_ALLOWLIST:
        raise McpEvidenceError(f"tool {tool!r} not on allowlist")
    t0 = time.perf_counter()
    r = await client.call_tool(tool, args)
    sc = r.structured_content
    if r.is_error or not isinstance(sc, dict):
        raise McpEvidenceError(f"{tool} failed: {(r.content[0].text if r.content else 'no content')[:200]}")
    log.append({"tool": tool, "args": args, "match_count": sc.get("match_count"),
                "ms": round((time.perf_counter() - t0) * 1000)})
    return sc


async def _gather(requirement: dict) -> DocsSnapshot:
    u = urlparse(DOCS_MCP_URL)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise McpEvidenceError(f"bad DOCS_MCP_URL {DOCS_MCP_URL!r}")

    log: list[dict] = []
    async with Client(DOCS_MCP_URL) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        missing = TOOL_ALLOWLIST - tools.keys()
        if missing:
            raise McpEvidenceError(f"MCP server lacks tools {sorted(missing)}")
        not_ro = [n for n in TOOL_ALLOWLIST if not (tools[n].annotations and tools[n].annotations.read_only_hint)]
        if not_ro:
            raise McpEvidenceError(f"tools not marked read-only: {not_ro}")

        status = await _call(client, log, "docs_status", {})
        ops: dict[tuple[str, str], Operation] = {}
        for req in knowledge.requirements_to_check(requirement):
            matched = knowledge.topics_in(req)
            if matched:
                for prefix in knowledge.TOPICS[matched[0]]["paths"]:
                    sc = await _call(client, log, "list_operations", {"path_prefix": prefix, "limit": 100})
                    for d in sc.get("operations", []):
                        ops[(d.get("title"), d.get("path"))] = _op_from(d)
            else:
                sc = await _call(client, log, "search_operations", {"phrase": req, "limit": 20})
                for d in sc.get("operations", []):
                    ops[(d.get("title"), d.get("path"))] = _op_from(d)

    return DocsSnapshot(
        operations=list(ops.values()),
        source_url=str(status.get("source_url", ""))[:200],
        retrieved_at=str(status.get("retrieved_at", ""))[:40],
        from_cache=bool(status.get("from_cache")),
        total_operations=int(status.get("total_operations") or 0),
        via="mcp:hackathon-ramp-docs",
        tool_calls=log,
    )


async def get_evidence(requirement: dict) -> tuple[DocsSnapshot | None, str | None]:
    """(snapshot, error). snapshot None -> caller falls back to the labeled fixture."""
    try:
        return await asyncio.wait_for(_gather(requirement), TIMEOUT_S), None
    except Exception as e:
        # anyio wraps failures in ExceptionGroups; surface the innermost message
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        return None, f"Docs MCP unavailable ({type(e).__name__}: {str(e)[:200]})"


class DocsSession:
    """One MCP connection to our docs server, scoped to a vendor.

        async with DocsSession("brex") as ds:
            await ds.list_operations("/v2/transactions")

    Only allowlisted, read-only tools are callable; every call is logged in tool_calls.
    Raises McpEvidenceError on connection or tool failures.
    """

    def __init__(self, vendor: str, url: str | None = None) -> None:
        self.vendor = vendor
        self.url = url or DOCS_MCP_URL
        self.tool_calls: list[dict] = []
        self._cm = None
        self._client = None

    async def __aenter__(self) -> "DocsSession":
        u = urlparse(self.url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise McpEvidenceError(f"bad docs MCP URL {self.url!r}")
        try:
            self._cm = Client(self.url)
            self._client = await self._cm.__aenter__()
            tools = {t.name: t for t in (await self._client.list_tools()).tools}
        except McpEvidenceError:
            raise
        except BaseException as e:  # connection failures surface as ExceptionGroups
            raise McpEvidenceError(_inner(e)) from None
        missing = TOOL_ALLOWLIST - tools.keys()
        not_ro = [n for n in TOOL_ALLOWLIST & tools.keys()
                  if not (tools[n].annotations and tools[n].annotations.read_only_hint)]
        if missing or not_ro:
            await self.__aexit__(None, None, None)
            raise McpEvidenceError(f"docs MCP server lacks read-only tools: {sorted(missing) or not_ro}")
        return self

    async def __aexit__(self, *exc) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except BaseException:
                pass
            self._cm = self._client = None

    async def _tool(self, tool: str, args: dict) -> dict:
        try:
            return await _call(self._client, self.tool_calls, tool, {**args, "vendor": self.vendor})
        except McpEvidenceError:
            raise
        except BaseException as e:
            raise McpEvidenceError(_inner(e)) from None

    async def status(self) -> dict:
        return await self._tool("docs_status", {})

    async def list_operations(self, prefix: str) -> dict:
        return await self._tool("list_operations", {"path_prefix": prefix, "limit": 100})

    async def search(self, phrase: str, in_description: bool = False, path_prefix: str | None = None) -> dict:
        return await self._tool("search_operations", {"phrase": phrase, "limit": 20, "in_description": in_description,
                                                      "path_prefix": path_prefix or ""})

    async def get_schema(self, name: str) -> dict:
        return await self._tool("get_schema", {"name": name})

    async def search_guides(self, phrase: str) -> dict:
        return await self._tool("search_guides", {"phrase": phrase, "limit": 10})


def _inner(e: BaseException) -> str:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return f"{type(e).__name__}: {str(e)[:200]}"
