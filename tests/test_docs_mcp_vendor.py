"""Vendor-aware docs MCP server, exercised through the real MCP client (DocsSession)
against a server process that reads a seeded offline cache."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from seller.docs_client import DocsSession, McpEvidenceError
from tests.conftest import seed_cache

ROOT = Path(__file__).resolve().parents[1]
PORT = 9195


@pytest.fixture(scope="module")
def docs_mcp_seeded(tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache")
    seed_cache(cache)
    env = {**os.environ, "DOCS_MCP_PORT": str(PORT), "JUDAS_CACHE_DIR": str(cache)}
    proc = subprocess.Popen([sys.executable, "-m", "docs_mcp.server"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{PORT}/mcp"
    for _ in range(60):
        try:
            httpx.get(url, timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("docs MCP server did not start")
    yield url
    proc.terminate(); proc.wait()


def _with(url, vendor, fn):
    async def go():
        async with DocsSession(vendor, url=url) as ds:
            return await fn(ds)
    return asyncio.run(go())


def test_brex_list_operations_via_mcp(docs_mcp_seeded):
    async def go():
        async with DocsSession("brex", url=docs_mcp_seeded) as ds:
            return await ds.list_operations("/v2/transactions"), ds.tool_calls
    r, calls = asyncio.run(go())
    op = next(o for o in r["operations"] if o["path"] == "/v2/transactions/card/primary")
    assert {"cursor", "limit", "posted_at_start"} <= set(op["query_params"])
    assert calls[-1]["tool"] == "list_operations"


def test_get_schema_money(docs_mcp_seeded):
    r = _with(docs_mcp_seeded, "brex", lambda ds: ds.get_schema("Money"))
    assert r["found"] and r["schema"]["fields"]["amount"].startswith("integer (int64)")


def test_description_search_finds_dedupe_guidance(docs_mcp_seeded):
    r = _with(docs_mcp_seeded, "brex", lambda ds: ds.search("de-duplicate", in_description=True))
    assert r["match_count"] >= 1 and "De-duplicate" in r["operations"][0]["snippet"]


def test_ramp_default_still_works(docs_mcp_seeded):
    r = _with(docs_mcp_seeded, "ramp", lambda ds: ds.list_operations("/developer/v1/webhooks"))
    assert r["match_count"] >= 1


def test_unknown_vendor_is_error(docs_mcp_seeded):
    with pytest.raises(McpEvidenceError):
        _with(docs_mcp_seeded, "acme", lambda ds: ds.status())


def test_search_scoped_to_path_prefix(docs_mcp_seeded):
    hit = _with(docs_mcp_seeded, "brex", lambda ds: ds.search("de-duplicate", in_description=True,
                                                                path_prefix="/v2/transactions"))
    miss = _with(docs_mcp_seeded, "brex", lambda ds: ds.search("de-duplicate", in_description=True,
                                                                 path_prefix="/v1/expenses"))
    assert hit["match_count"] >= 1 and miss["match_count"] == 0


def test_get_schema_reports_its_own_source(docs_mcp_seeded):
    r = _with(docs_mcp_seeded, "brex", lambda ds: ds.get_schema("Money"))
    assert r["schema"]["source_url"].endswith("transactions_api.yaml")
