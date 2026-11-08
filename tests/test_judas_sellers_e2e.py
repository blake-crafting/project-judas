"""Real A2A e2e: Judas sends buyer_requirements to the Ramp and Brex seller processes,
which answer from official docs via the docs MCP server (seeded offline cache)."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from buyer.client import ask_seller, load_registry, new_requirement, send_to_seller
from judas.contracts import BUYER_REQUIREMENTS, ERROR, VENDOR_RESPONSE, envelope, validate
from judas.questions import questions_payload
from tests.conftest import seed_cache

ROOT = Path(__file__).resolve().parents[1]
P_MCP, P_RAMP, P_BREX = 9194, 9192, 9193


def _spawn(module, env, probe):
    proc = subprocess.Popen([sys.executable, "-m", module], cwd=ROOT, env={**os.environ, **env},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            httpx.get(probe, timeout=0.5)
            return proc
        except httpx.HTTPError:
            time.sleep(0.25)
    proc.kill()
    pytest.fail(f"{module} did not start")


@pytest.fixture(scope="module")
def sellers(tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache")
    seed_cache(cache)
    mcp_url = f"http://127.0.0.1:{P_MCP}/mcp"
    procs = [
        _spawn("docs_mcp.server", {"DOCS_MCP_PORT": str(P_MCP), "JUDAS_CACHE_DIR": str(cache)}, mcp_url),
        _spawn("seller.server", {"SELLER_PORT": str(P_RAMP), "DOCS_MCP_URL": mcp_url, "EVIDENCE_SOURCE": "mcp",
                                 "JUDAS_CACHE_DIR": str(cache)},
               f"http://127.0.0.1:{P_RAMP}/.well-known/agent-card.json"),
        _spawn("seller.vendor_server", {"BREX_PORT": str(P_BREX), "DOCS_MCP_URL": mcp_url},
               f"http://127.0.0.1:{P_BREX}/.well-known/agent-card.json"),
    ]
    reg = load_registry()
    ports = {"ramp-docs-adapter": P_RAMP, "brex-docs-adapter": P_BREX}
    reg = [{**a, "base_url": f"http://127.0.0.1:{ports[a['id']]}"} if a["id"] in ports else a for a in reg]
    yield reg
    for p in procs:
        p.terminate(); p.wait()


def _req(eid="eval-t1"):
    return envelope(BUYER_REQUIREMENTS, eid, buyer={"name": "Fictional Northstar Finance", "employees": 300,
                                                    "synthetic": True},
                    request="sync expenses", questions=questions_payload())


def test_registry_has_both_vendor_sellers():
    reg = {a["id"]: a for a in load_registry()}
    assert reg["brex-docs-adapter"]["vendor"] == "brex" and reg["ramp-docs-adapter"]["vendor"] == "ramp"
    assert reg["brex-docs-adapter"]["base_url"] == "http://127.0.0.1:9104"


def test_both_vendors_answer_buyer_requirements(sellers):
    for agent in ("ramp-docs-adapter", "brex-docs-adapter"):
        out = asyncio.run(send_to_seller(agent, _req(), context_id="eval-t1", agents=sellers))
        r = out["response"]
        assert r["schema"] == VENDOR_RESPONSE and validate(r) == [] and len(r["answers"]) == 7, r
        assert any(a["finding"] == "documented" for a in r["answers"])
        assert out["trace"][-1]["state"] == "TASK_STATE_COMPLETED"
        assert r["tool_calls"], "seller must log its MCP calls"


def test_brex_card_is_labeled_not_operated_by_brex(sellers):
    card = httpx.get(f"http://127.0.0.1:{P_BREX}/.well-known/agent-card.json").json()
    assert card["name"] == "Brex Developer Documentation Advisor"
    assert card["description"].startswith("Hackathon-owned adapter, NOT operated by Brex.")


def test_unknown_schema_returns_error_artifact(sellers):
    out = asyncio.run(send_to_seller("brex-docs-adapter", {"schema": "judas.bogus/v1"}, agents=sellers))
    assert out["response"]["schema"] == ERROR and "unsupported schema" in out["response"]["message"]


def test_legacy_plain_request_still_answered(sellers):
    out = asyncio.run(ask_seller("ramp-docs-adapter", new_requirement("vendors?", ["vendor records"]), sellers))
    assert out["response"]["vendor_id"] == "ramp-docs-adapter"
