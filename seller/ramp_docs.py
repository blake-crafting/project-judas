"""Live, read-only access to Ramp's official public API reference.

WHY NOT MCP: the handoff planned to use Ramp's docs-only "Developer MCP" at
https://mcp.ramp.com/developer/mcp. On 2026-11-07 that URL returns HTTP 404,
and Ramp's docs (https://docs.ramp.com/llms-guides/mcp.txt) now say coding
agents should read the developer docs directly from https://docs.ramp.com/llms.txt.
Ramp's remaining MCP servers need a real Ramp account (OAuth) or a
partner API key, which this prototype must not use.

So this module does a plain HTTPS GET of Ramp's machine-readable API reference
(llms-api.txt). Provenance label: "official_docs", retrieved over HTTP, NOT
via an MCP tool.

Security: one hard-coded allowlisted URL, size cap, timeout. The text is
treated as untrusted DATA: we extract fields with string parsing and never
act on instructions inside it.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

SOURCE_URL = "https://docs.ramp.com/llms-api.txt"   # the ONLY URL this module fetches
MAX_BYTES = 8_000_000
TIMEOUT_S = 15
CACHE_TTL_S = 6 * 3600
CACHE_DIR = Path(os.environ.get("JUDAS_CACHE_DIR", Path(__file__).resolve().parents[1] / "data" / "cache"))


@dataclass
class Operation:
    title: str
    path: str
    description: str
    required_fields: list[str] = field(default_factory=list)
    optional_fields: list[str] = field(default_factory=list)
    method: str = ""                       # "" when the source doesn't state it (Ramp llms-api.txt)
    query_params: list[str] = field(default_factory=list)
    response_fields: list[str] = field(default_factory=list)   # "name[?]: type"
    source_url: str = ""                   # the official doc this operation came from

    def as_evidence(self) -> dict:
        return {
            "title": self.title,
            "path": self.path,
            "method": self.method,
            "required_request_fields": self.required_fields,
            "query_params": self.query_params,
            "response_fields": self.response_fields,
            "source_url": self.source_url,
            "description": self.description[:240],
        }


@dataclass
class SchemaDef:
    name: str
    fields: dict[str, str]                 # field -> "<type>[ (<format>)] — <description>"
    description: str = ""
    source_url: str = ""                   # the official spec this schema came from
    sources: list[str] = field(default_factory=list)   # all specs that contributed (merged schemas)


@dataclass
class DocsSnapshot:
    operations: list[Operation]
    source_url: str
    retrieved_at: str
    from_cache: bool
    total_operations: int | None = None   # set when `operations` is a subset (MCP results)
    via: str | None = None                 # e.g. "mcp:hackathon-ramp-docs"; None = direct HTTPS
    tool_calls: list[dict] = field(default_factory=list)
    vendor: str = "ramp"
    schemas: dict[str, SchemaDef] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.total_operations if self.total_operations is not None else len(self.operations)


def _top_level_fields(block: str) -> tuple[list[str], list[str]]:
    """Return (required, optional) top-level keys of a request schema block.
    Nesting isn't indented in the source, so track brace depth."""
    required, optional = [], []
    depth = 0
    for line in block.splitlines():
        s = line.strip()
        if depth == 1:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(\??):", s)
            if m:
                (optional if m.group(2) else required).append(m.group(1))
        depth += s.count("{") - s.count("}")
    return required[:30], optional[:30]


def _response_fields(block: str) -> list[str]:
    """Response keys as "name[?]: type": depth-1 scalars plus everything at depth 2
    (list items like data:{...}[] and page:{next}). Nested objects show as "object"."""
    out: list[str] = []
    depth = 0
    for line in block.splitlines():
        s = line.strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*\??):\s*(.*)$", s)
        if m and depth in (1, 2):
            typ = m.group(2).strip()
            container = typ.startswith("{")
            if depth == 2 or not container:
                out.append(f"{m.group(1)}: {'object' if container else typ[:80]}")
        depth += s.count("{") - s.count("}")
    return out[:60]


def parse(text: str) -> list[Operation]:
    ops: list[Operation] = []
    for chunk in re.split(r"\n-{3,}\n", text):
        title = re.search(r"^title: (.+)$", chunk, re.M)
        path = re.search(r"^path: (\S+)$", chunk, re.M)
        if not (title and path):
            continue
        desc = re.search(r"^description: (.*?)(?=^request:|\Z)", chunk, re.M | re.S)
        req = re.search(r"^request: (.*?)(?=^response:|\Z)", chunk, re.M | re.S)
        resp = re.search(r"^response: (.*)\Z", chunk, re.M | re.S)
        required, optional = ([], [])
        if req and req.group(1).strip() != "none":
            required, optional = _top_level_fields(req.group(1))
        description = (desc.group(1).strip() if desc else "")
        ops.append(Operation(
            title=title.group(1).strip()[:200],
            path=path.group(1).strip()[:200],
            description="" if description == "No description" else description,
            required_fields=required,
            optional_fields=optional,
            response_fields=_response_fields(resp.group(1)) if resp and resp.group(1).strip() != "none" else [],
            source_url=SOURCE_URL,
        ))
    return ops


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


REFRESH_BACKOFF_S = 600          # after a failed refresh, serve the stale cache without retrying
CACHED_TIMEOUT_S = 5             # shorter network timeout when a usable cache exists
_refresh_failed_at: float | None = None


def _read_cache() -> tuple[str, dict] | None:
    body, meta = CACHE_DIR / "llms-api.txt", CACHE_DIR / "llms-api.meta.json"
    if body.exists() and meta.exists():
        return body.read_text(), json.loads(meta.read_text())
    return None


def _write_cache(text: str, retrieved_at: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "llms-api.txt").write_text(text)
    (CACHE_DIR / "llms-api.meta.json").write_text(
        json.dumps({"retrieved_at": retrieved_at, "epoch": time.time(), "source_url": SOURCE_URL}))


async def _download(timeout: float | None = None) -> str:
    async with httpx.AsyncClient(timeout=timeout or TIMEOUT_S, follow_redirects=False) as http:
        async with http.stream("GET", SOURCE_URL) as r:
            r.raise_for_status()
            buf = bytearray()
            async for part in r.aiter_bytes():
                buf.extend(part)
                if len(buf) > MAX_BYTES:
                    raise ValueError("Ramp docs response exceeded size cap")
            return buf.decode("utf-8", errors="replace")


async def get_snapshot(force_refresh: bool = False) -> tuple[DocsSnapshot | None, str | None]:
    """Return (snapshot, error). snapshot is None only if live docs are
    unavailable AND there is no cache. Set LIVE_DOCS=off to force that."""
    if os.environ.get("LIVE_DOCS", "on").lower() == "off":
        return None, "Live docs disabled (LIVE_DOCS=off)"

    global _refresh_failed_at
    cached = _read_cache()
    if cached and not force_refresh and time.time() - cached[1].get("epoch", 0) < CACHE_TTL_S:
        return DocsSnapshot(parse(cached[0]), SOURCE_URL, cached[1]["retrieved_at"], True), None
    if cached and not force_refresh and _refresh_failed_at and time.time() - _refresh_failed_at < REFRESH_BACKOFF_S:
        return (DocsSnapshot(parse(cached[0]), SOURCE_URL, cached[1]["retrieved_at"], True),
                "refresh skipped after a recent failure; serving cached docs")

    try:
        text = await _download(CACHED_TIMEOUT_S if cached else None)
        ops = parse(text)
        if len(ops) < 10:
            raise ValueError(f"Ramp docs parsed to only {len(ops)} operations; format may have changed")
        retrieved_at = _now()
        _write_cache(text, retrieved_at)
        _refresh_failed_at = None
        return DocsSnapshot(ops, SOURCE_URL, retrieved_at, False), None
    except Exception as e:  # network, HTTP, size, format
        _refresh_failed_at = time.time()
        err = f"{type(e).__name__}: {e}"[:300]
        if cached:  # stale cache beats nothing, and is labeled as such
            return DocsSnapshot(parse(cached[0]), SOURCE_URL, cached[1]["retrieved_at"], True), err
        return None, err
