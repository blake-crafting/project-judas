"""Tests: seller logic (unit), live Ramp docs, and REAL buyer->seller A2A
exchanges (end-to-end, seller launched as a subprocess). No mocks of the
network exchange. Tests marked `live` hit docs.ramp.com.
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from buyer.client import ask_seller, load_registry, new_requirement, search_registry
from seller import knowledge, ramp_docs
from seller.ramp_docs import DocsSnapshot

ROOT = Path(__file__).resolve().parents[1]
PORT_OFFLINE, PORT_LIVE, PORT_MCP, PORT_DEAD = 9198, 9199, 9197, 9196

# A tiny excerpt in the same format as https://docs.ramp.com/llms-api.txt (test data).
SAMPLE_DOCS = """title: List vendors
path: /developer/v1/vendors
description: No description

request: none

response: { data: { id: string }[] }


---

title: Create a new vendor
path: /developer/v1/vendors
description: Create a vendor.

request: {
  business_vendor_contacts: {
  email: string
  name?: string
}[]
  name?: string
}

response: { id: string }


---

title: Creates a new webhook subscription
path: /developer/v1/webhooks
description: No description

request: {
  endpoint_url: string
  event_types: string[]
  include_payload?: boolean
}

response: none
"""


def sample_snapshot() -> DocsSnapshot:
    return DocsSnapshot(ramp_docs.parse(SAMPLE_DOCS), ramp_docs.SOURCE_URL, "2026-11-07T00:00:00+00:00", False)


# ---------- unit: docs parser ----------

def test_parser_extracts_only_top_level_required_fields():
    ops = {o.title: o for o in ramp_docs.parse(SAMPLE_DOCS)}
    assert set(ops) == {"List vendors", "Create a new vendor", "Creates a new webhook subscription"}
    assert ops["Create a new vendor"].required_fields == ["business_vendor_contacts"]  # not nested 'email'
    assert ops["Creates a new webhook subscription"].required_fields == ["endpoint_url", "event_types"]
    assert ops["List vendors"].required_fields == []


# ---------- unit: answer engine with live-format docs ----------

def test_live_claims_cite_exact_operations_and_fields():
    r = knowledge.answer(new_requirement("Can I create vendors?", ["vendor records"]), sample_snapshot())
    c = r["claims"][0]
    assert r["evidence_mode"] == "live_official_docs"
    assert c["finding"] == "documented" and c["source_type"] == "official_docs" and c["verified"] is True
    assert c["observed_via_tool"] is False  # HTTP fetch, not an MCP tool
    assert c["evidence"][0]["title"] == "Create a new vendor"  # ranked by question words
    assert c["evidence"][0]["required_request_fields"] == ["business_vendor_contacts"]


def test_missing_topic_in_live_docs_is_unknown_not_guessed():
    r = knowledge.answer(new_requirement("q", ["bills", "payroll"]), sample_snapshot())
    findings = {c["requirement"]: c["finding"] for c in r["claims"]}
    assert findings == {"bills": "unknown", "payroll": "unknown"}  # sample has no bills section
    assert r["status"] == "unanswered" and r["unanswered"] == ["bills", "payroll"]


def test_write_action_is_unsupported_but_docs_question_about_payments_is_not():
    declined = knowledge.answer(new_requirement("Please approve a payment of $5,000"), sample_snapshot())
    assert declined["status"] == "declined"
    docs_q = knowledge.answer(new_requirement("Is there an API for purchase orders and payments?"), sample_snapshot())
    assert all(c["finding"] != "unsupported" for c in docs_q["claims"])


def test_free_text_never_drops_unknown_topics():
    r = knowledge.answer(new_requirement("Do you document vendors and run payroll?"), sample_snapshot())
    assert {c["requirement"]: c["finding"] for c in r["claims"]} == {
        "vendor records": "documented", "payroll": "unknown"}


# ---------- unit: fallback when live docs are unavailable ----------

def test_fallback_is_labeled_unavailable_and_unverified():
    r = knowledge.answer(new_requirement("q", ["vendor records", "webhooks"]), None, "ConnectError: boom")
    assert r["evidence_mode"] == "local_fixture"
    assert r["limitations"][0].startswith("Live vendor docs unavailable (ConnectError: boom)")
    vendor, webhooks = r["claims"]
    assert vendor["source_type"] == "local_fixture" and vendor["verified"] is False
    assert webhooks["finding"] == "unknown"


def test_registry_search_ranks_real_seller_first_and_ignores_irrelevant():
    hits = search_registry("Ramp API for expenses and vendors")
    assert hits[0]["id"] == "ramp-docs-adapter"
    assert search_registry("quantum knitting patterns") == []


# ---------- live: real fetch from docs.ramp.com ----------

@pytest.mark.live
def test_live_ramp_docs_fetch_and_parse():
    snap, err = asyncio.run(ramp_docs.get_snapshot(force_refresh=True))
    assert snap is not None and err is None, err
    assert snap.from_cache is False  # proves a real network fetch happened
    assert len(snap.operations) > 100
    paths = {o.path for o in snap.operations}
    assert {"/developer/v1/vendors", "/developer/v1/transactions", "/developer/v1/bills"} <= paths


# ---------- e2e: real A2A over HTTP ----------

def _wait_for(url: str, proc) -> None:
    for _ in range(60):
        try:
            httpx.get(url, timeout=0.5)
            return
        except httpx.HTTPError:
            time.sleep(0.25)
    proc.kill()
    pytest.fail(f"server at {url} did not start")


def _start_seller(port: int, evidence: str, mcp_port: int | None = None):
    env = {**os.environ, "SELLER_PORT": str(port), "EVIDENCE_SOURCE": evidence}
    if mcp_port:
        env["DOCS_MCP_URL"] = f"http://127.0.0.1:{mcp_port}/mcp"
    proc = subprocess.Popen([sys.executable, "-m", "seller.server"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    _wait_for(f"{url}/.well-known/agent-card.json", proc)
    reg = load_registry()
    reg[0] = {**reg[0], "base_url": url}  # point the real entry at the test server
    return proc, reg


@pytest.fixture(scope="module")
def docs_mcp_server():
    env = {**os.environ, "DOCS_MCP_PORT": str(PORT_MCP)}
    proc = subprocess.Popen([sys.executable, "-m", "docs_mcp.server"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{PORT_MCP}/mcp"
    _wait_for(url, proc)
    yield url
    proc.terminate(); proc.wait()


@pytest.fixture(scope="module")
def offline_agents():
    proc, reg = _start_seller(PORT_OFFLINE, evidence="off")
    yield reg
    proc.terminate(); proc.wait()


@pytest.fixture(scope="module")
def live_agents(docs_mcp_server):
    proc, reg = _start_seller(PORT_LIVE, evidence="mcp", mcp_port=PORT_MCP)
    yield reg
    proc.terminate(); proc.wait()


def test_e2e_exchange_trace_and_fallback_labeling(offline_agents):
    req = new_requirement("Does Ramp expose API operations for expense and vendor records?",
                          ["vendor records", "expenses", "payroll"])
    out = asyncio.run(ask_seller("ramp-docs-adapter", req, offline_agents))
    resp, steps = out["response"], [s["step"] for s in out["trace"]]
    assert steps == ["agent_card_fetched", "agent_card_validated", "question_sent",
                     "seller_answer_received"]
    assert out["trace"][-1]["state"] == "TASK_STATE_COMPLETED"
    assert resp["request_id"] == req["request_id"]  # round-tripped through the server
    assert resp["evidence_mode"] == "local_fixture"
    assert "Live vendor docs unavailable" in resp["limitations"][0]
    assert resp["unanswered"] == ["payroll"]


@pytest.mark.live
def test_e2e_live_docs_evidence_over_a2a(live_agents):
    req = new_requirement("Show the exact documented API operation and required fields to create a bill",
                          ["bills", "payroll"])
    resp = asyncio.run(ask_seller("ramp-docs-adapter", req, live_agents))["response"]
    assert resp["evidence_mode"] == "live_official_docs"
    bills = resp["claims"][0]
    assert bills["finding"] == "documented" and bills["source_type"] == "official_docs"
    top = bills["evidence"][0]
    assert top["title"] == "Create a bill" and "vendor_id" in top["required_request_fields"]
    assert bills["via"] == "mcp:hackathon-ramp-docs" and bills["observed_via_tool"] is True
    assert [c["tool"] for c in resp["tool_calls"]] == ["docs_status", "list_operations", "search_operations"]
    assert resp["unanswered"] == ["payroll"]
    assert any("not a Ramp MCP server" in l for l in resp["limitations"])


@pytest.mark.live
def test_docs_mcp_server_tools_are_read_only_and_work(docs_mcp_server):
    from mcp import Client

    async def run():
        async with Client(docs_mcp_server) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert set(tools) == {"docs_status", "list_operations", "search_operations", "get_operation", "get_schema",
                              "guide_status", "search_guides"}
            assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint
                       for t in tools.values())
            op = (await c.call_tool("get_operation", {"title": "Create a bill"})).structured_content
            assert op["found"] and op["operations"][0]["path"] == "/developer/v1/bills"
            none = (await c.call_tool("list_operations", {"path_prefix": "/developer/v1/payroll"})).structured_content
            assert none["match_count"] == 0
    asyncio.run(run())


def test_e2e_mcp_down_falls_back_with_label():
    """Seller configured for MCP, but nothing listens on the MCP port."""
    proc, reg = _start_seller(PORT_LIVE + 10, evidence="mcp", mcp_port=PORT_DEAD)
    try:
        resp = asyncio.run(ask_seller("ramp-docs-adapter", new_requirement("q", ["bills"]), reg))["response"]
    finally:
        proc.terminate(); proc.wait()
    assert resp["evidence_mode"] == "local_fixture"
    assert resp["limitations"][0].startswith("Live vendor docs unavailable (Docs MCP unavailable")
    assert resp["claims"][0]["verified"] is False and resp["tool_calls"] == []


def test_refuses_card_endpoint_outside_allowlist(offline_agents):
    """Registry lists the seller as localhost:PORT, but its card advertises
    127.0.0.1:PORT. The buyer must not follow an endpoint it wasn't told about."""
    reg = [{**offline_agents[0], "base_url": f"http://localhost:{PORT_OFFLINE}"}]
    with pytest.raises(PermissionError, match="allowlist"):
        asyncio.run(ask_seller("ramp-docs-adapter", new_requirement("vendors?"), reg))


def test_agent_without_endpoint_cannot_be_contacted(offline_agents):
    reg = [{**a, "base_url": None} if a["id"] == "payroll-pal-demo" else a for a in offline_agents]
    with pytest.raises(RuntimeError, match="no running endpoint"):
        asyncio.run(ask_seller("payroll-pal-demo", new_requirement("payroll?"), reg))
