"""Judas evaluator upgrade: official guides via MCP, synthetic demos via a real sandbox MCP server,
buyer priorities that change what is asked and how the verdict is reached."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from judas import evaluation
from judas.contracts import validate
from seller import vendor_guides
from seller.docs_client import DocsSession
from seller.vendors.demo import run_demo
from tests.conftest import seed_cache

ROOT = Path(__file__).resolve().parents[1]


def _serve(module: str, port_env: str, port: int, env_extra: dict | None = None):
    env = {**os.environ, port_env: str(port), **(env_extra or {})}
    proc = subprocess.Popen([sys.executable, "-m", module], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(60):
        try:
            httpx.get(url, timeout=0.5)
            return proc, url
        except httpx.HTTPError:
            time.sleep(0.25)
    proc.kill()
    pytest.fail(f"{module} did not start")


@pytest.fixture(scope="module")
def sandbox():
    proc, url = _serve("sandbox_mcp.server", "SANDBOX_MCP_PORT", 9196)
    yield url
    proc.terminate(); proc.wait()


@pytest.fixture(scope="module")
def docs_with_guides(tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache")
    seed_cache(cache)
    proc, url = _serve("docs_mcp.server", "DOCS_MCP_PORT", 9197, {"JUDAS_CACHE_DIR": str(cache)})
    yield url
    proc.terminate(); proc.wait()


def test_demo_runs_real_mcp_calls_on_synthetic_data_and_says_so(sandbox):
    for vendor, name in (("ramp", "Ramp"), ("brex", "Brex")):
        res = asyncio.run(run_demo({"evaluation_id": "eval-t"}, vendor, name, url=sandbox))
        assert validate(res) == []
        assert res["evidence_level"] == "synthetic_demo" and res["provenance"] == "synthetic_fixture"
        assert res["endpoint"]["vendor_hosted"] is False and "SYNTHETIC" in res["disclaimer"]
        assert len(res["tool_calls"]) >= 5                       # info + 4 pages, all real MCP calls
        assert res["duplicates_removed"] == 2 and res["unique_records"] == 6   # same scenario for both vendors
        jpy = next(n for n in res["normalized_sample"] + [] if n["currency"] == "JPY") if any(
            n["currency"] == "JPY" for n in res["normalized_sample"]) else None
        if jpy:
            assert jpy["minor_units"] == 15000                    # zero-decimal currency handled
        assert any("does not prove" in l for l in res["limitations"])


def test_ramp_demo_reads_the_current_amount_field(sandbox):
    res = asyncio.run(run_demo({"evaluation_id": "eval-t"}, "ramp", "Ramp", url=sandbox))
    assert "entity_amount" in res["sample_record"] and "entity_amount" in res["observations"][2]["text"]


def test_guides_are_searchable_through_mcp_with_official_sources(docs_with_guides):
    async def go():
        async with DocsSession("ramp", url=docs_with_guides) as ds:
            return await ds.search_guides("transactions:read")
    res = asyncio.run(go())
    assert res["match_count"] >= 1 and res["passages"][0]["source_url"] in vendor_guides.GUIDES["ramp"]


def test_guide_fetches_are_allowlisted():
    with pytest.raises(PermissionError):
        asyncio.run(vendor_guides._download("https://example.com/llms.txt", 1))


def test_json_spec_keeps_only_transaction_passages():
    text = ('{"paths": {"/developer/v1/transactions": {"get": {"parameters": [{"name": "from_date", '
            '"description": "after date"}]}}, "/developer/v1/bills": {"get": {"description": "bills"}}}}')
    titles = [p.title for p in vendor_guides.parse_guide(text, "https://x/spec.json", "ramp")]
    assert any("?from_date" in t for t in titles) and not any("bills" in t for t in titles)


def test_skipped_requirements_are_not_sent_and_unskipping_reasks():
    ev = evaluation.new_evaluation("sync", priorities={"Q6": "skip", "Q7": "skip"})
    assert [q["id"] for q in ev["requirements"]["questions"]] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert all("priority" in q for q in ev["requirements"]["questions"])
    assert validate(ev["requirements"]) == []
    ev["responses"] = {"ramp": {"x": 1}}
    assert evaluation.set_priorities(ev, {"Q6": "must", "Q7": "skip"}) is True and ev["responses"] == {}
    ev["responses"] = {"ramp": {"x": 1}}
    assert evaluation.set_priorities(ev, {"Q6": "nice", "Q7": "skip"}) is False and ev["responses"]   # verdict only


def test_bad_priorities_are_rejected_by_the_contract():
    ev = evaluation.new_evaluation("sync")
    assert validate({**ev["requirements"], "priorities": {"Q1": "critical"}})


# ---------- Challenge: one focused follow-up on an unresolved requirement ----------

from judas.contracts import FOLLOW_UP, FOLLOW_UP_RESULT, envelope  # noqa: E402
from seller.vendors.common import build_follow_up  # noqa: E402
from seller.vendors.ramp import RAMP  # noqa: E402
from tests.test_vendor_answers import RAMP_GUIDES, FakeDocs, fake_ramp  # noqa: E402

STABILITY = "id documented as stable across the transaction lifecycle"


def _follow_up(qid, missing):
    return envelope(FOLLOW_UP, "eval-t", question_id=qid, missing=missing,
                    ask=f"Your answer did not establish: {'; '.join(missing)}. What official evidence supports it?")


def test_follow_up_contract():
    assert validate(_follow_up("Q5", [STABILITY])) == []
    assert validate(_follow_up("Q9", [STABILITY]))            # unknown question


def test_follow_up_still_missing_reports_what_was_searched_and_loose_hits_are_not_counted():
    res = asyncio.run(build_follow_up(_follow_up("Q5", [STABILITY]), RAMP, session_factory=fake_ramp))
    assert res["schema"] == FOLLOW_UP_RESULT and validate(res) == []
    inv = res["investigations"][0]
    assert inv["facet"] == STABILITY and inv["resolved"] is False and inv["searched"]
    # "The same event id is used for all retry attempts" shares words but is about webhooks: a candidate, not evidence
    assert all(c["counted"] is False for c in inv["candidates"])
    assert res["answer"]["finding"] == "partially_documented"


def test_follow_up_resolves_only_on_a_strict_match():
    guides = {**RAMP_GUIDES, "transaction id": [{
        "title": "components/schemas/Transaction/properties/id", "source_url": "https://docs.ramp.com/openapi/developer-api.json",
        "snippet": "The transaction id does not change when the transaction clears.",
        "text": "The transaction id does not change when the transaction clears."}]}
    docs = FakeDocs(guides=guides, lists=fake_ramp("ramp").lists)
    res = asyncio.run(build_follow_up(_follow_up("Q5", [STABILITY]), RAMP, session_factory=lambda v: docs))
    inv = res["investigations"][0]
    assert inv["resolved"] is True and res["answer"]["finding"] == "documented"
    assert any(e["source"].endswith("developer-api.json") for e in res["answer"]["evidence"])


def _eval_with_partial_q5():
    ev = evaluation.new_evaluation("sync")
    ans = {"question_id": "Q5", "finding": "partially_documented", "answer": "✓ id · ✗ stable",
           "evidence": [{"provenance": "real_mcp_response", "source": "https://docs.ramp.com/llms-api.txt"}],
           "facets": [{"name": "transaction id field", "kind": "api_primitive", "core": True, "found": True,
                       "evidence": [{"provenance": "real_mcp_response", "source": "https://docs.ramp.com/llms-api.txt"}]},
                      {"name": STABILITY, "kind": "guarantee", "core": True, "found": False, "evidence": []}]}
    ev["responses"]["ramp"] = envelope("judas.vendor_response/v1", ev["evaluation_id"], "ramp", answers=[ans], limitations=[])
    return ev


def test_challenge_sends_the_missing_parts_keeps_the_original_and_recompares(monkeypatch, tmp_path):
    from web import events
    monkeypatch.setattr(events, "EVENTS_PATH", tmp_path / "events.jsonl")
    ev = _eval_with_partial_q5()
    sent = {}

    async def fake_send(agent_id, payload, context_id=None):
        sent.update(payload)
        new = {**ev["responses"]["ramp"]["answers"][0], "answer": "re-checked"}
        return {"response": envelope(FOLLOW_UP_RESULT, payload["evaluation_id"], "ramp", question_id="Q5", answer=new,
                                     investigations=[{"facet": STABILITY, "resolved": False, "searched": ["guides: id"],
                                                      "candidates": []}], tool_calls=[])}
    monkeypatch.setattr(evaluation, "send_to_seller", fake_send)
    res = asyncio.run(evaluation.challenge(ev, "ramp", "Q5"))
    assert sent["schema"] == FOLLOW_UP and sent["missing"] == [STABILITY]
    assert res["schema"] == FOLLOW_UP_RESULT
    fu = ev["follow_ups"][0]
    assert fu["original_answer"]["answer"] == "✓ id · ✗ stable"          # original evidence preserved
    assert ev["responses"]["ramp"]["answers"][0]["answer"] == "re-checked"
    cell = next(r for r in ev["comparison"]["rows"] if r["question_id"] == "Q5")["ramp"]
    assert cell["follow_up"]["still_missing"] == [STABILITY]


def test_challenge_with_nothing_missing_or_an_invalid_reply_changes_nothing(monkeypatch, tmp_path):
    from web import events
    monkeypatch.setattr(events, "EVENTS_PATH", tmp_path / "events.jsonl")
    ev = _eval_with_partial_q5()
    assert asyncio.run(evaluation.challenge(ev, "ramp", "Q1"))["schema"] == "judas.error/v1"   # no answer to challenge

    async def bad_send(agent_id, payload, context_id=None):
        return {"response": {"schema": FOLLOW_UP_RESULT, "evaluation_id": "x"}}
    monkeypatch.setattr(evaluation, "send_to_seller", bad_send)
    before = ev["responses"]["ramp"]["answers"][0]
    assert asyncio.run(evaluation.challenge(ev, "ramp", "Q5"))["schema"] == "judas.error/v1"
    assert ev["responses"]["ramp"]["answers"][0] is before


def test_challenge_never_resolves_a_settled_event_from_an_unrelated_webhook_passage():
    # Found live: a Brex Cash transfer event mentions "transactions" in the webhooks spec. That is not a
    # card-transaction settled event and must stay unresolved (it may appear only as a lead, not counted).
    from seller.vendors.brex import BREX
    from tests.test_vendor_answers import fake_brex
    hook = {"title": "x-webhooks//x-webhooks/transferFailed/post", "source_url":
            "https://developer.brex.com/_spec/openapi/webhooks_api.yaml",
            "snippet": "Transfer failed events for both incoming and outgoing Brex Cash transactions.",
            "text": "Transfer failed events for both incoming and outgoing Brex Cash transactions."}
    base = fake_brex("brex")
    docs = FakeDocs(lists=base.lists, schemas=base.schemas, searches=base.searches,
                    guides={"TRANSACTION": [hook], "card transaction": [hook]})
    name = "event when a card transaction settles (clears)"
    res = asyncio.run(build_follow_up(_follow_up("Q6", [name]), BREX, session_factory=lambda v: docs))
    inv = res["investigations"][0]
    assert inv["resolved"] is False and res["answer"]["finding"] != "documented"
    assert any("Brex Cash" in c["snippet"] and c["counted"] is False for c in inv["candidates"])


# ---------- priorities suggested from the buyer's own words (rule-based, reviewable) ----------

from judas.priorities import suggest  # noqa: E402


def test_suggest_reads_the_buyers_words_and_explains_each_change():
    s = suggest("We care more about real-time expense updates than the simplest implementation. "
                "We also cannot tolerate duplicated transactions.")
    assert s["priorities"]["Q6"] == "must" and s["priorities"]["Q5"] == "must"
    assert {r["question_id"] for r in s["reasons"]} >= {"Q5", "Q6"}
    assert all(r["matched"] for r in s["reasons"])                 # every change cites the words that caused it


def test_suggest_handles_negation_and_leaves_defaults_alone():
    s = suggest("Nightly batch is fine; we don't need real-time updates.")
    assert s["priorities"]["Q6"] == "nice"
    plain = suggest("Hello")
    assert plain["reasons"] == [] and plain["priorities"] == evaluation.clean_priorities(None)
