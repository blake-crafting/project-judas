"""Seller answer builder: answers come only from docs lookups (fake MCP session here)."""

import asyncio

from judas.contracts import BUYER_REQUIREMENTS, envelope, validate
from judas.questions import questions_payload
from seller.docs_client import McpEvidenceError
from seller.vendors.brex import BREX
from seller.vendors.common import VIA, build_vendor_response
from seller.vendors.ramp import RAMP

RAMP_URL = "https://docs.ramp.com/llms-api.txt"
BREX_URL = "https://developer.brex.com/_spec/openapi/transactions_api.yaml"
META = {"retrieved_at": "2026-11-07T00:00:00+00:00"}


def req(custom=None):
    extra = {"custom_question": custom} if custom else {}
    return envelope(BUYER_REQUIREMENTS, "eval-t", buyer={"name": "x"}, request="sync", questions=questions_payload(), **extra)


class FakeDocs:
    def __init__(self, lists=None, schemas=None, searches=None, guides=None):
        self.lists, self.schemas, self.searches = lists or {}, schemas or {}, searches or {}
        self.guides = guides or {}
        self.tool_calls = []

    async def search_guides(self, phrase):
        self.tool_calls.append({"tool": "search_guides", "args": {"phrase": phrase}})
        hits = self.guides.get(phrase, [])
        return {**META, "match_count": len(hits), "passages": hits}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def list_operations(self, prefix):
        self.tool_calls.append({"tool": "list_operations", "args": {"path_prefix": prefix}})
        ops = self.lists.get(prefix, [])
        return {**META, "source_url": ops[0]["source_url"] if ops else "", "match_count": len(ops), "operations": ops}

    async def search(self, phrase, in_description=False, path_prefix=None):
        self.tool_calls.append({"tool": "search_operations", "args": {"phrase": phrase, "path_prefix": path_prefix}})
        ops = self.searches.get(phrase, [])
        return {**META, "source_url": "", "match_count": len(ops), "operations": ops}

    async def get_schema(self, name):
        self.tool_calls.append({"tool": "get_schema", "args": {"name": name}})
        fields = self.schemas.get(name)
        return {**META, "source_url": BREX_URL, "found": fields is not None,
                "schema": {"name": name, "fields": fields, "description": ""} if fields else None}


BREX_OP = {"title": "List primary card transactions", "path": "/v2/transactions/card/primary", "method": "GET",
           "query_params": ["cursor", "limit", "posted_at_start"], "response_fields": ["id: string"], "source_url": BREX_URL}
RAMP_OP = {"title": "List transactions", "path": "/developer/v1/transactions", "method": "", "query_params": [],
           "response_fields": ["id?: string", "amount?: number", "currency_code?: string",
                               "minor_unit_conversion_rate: number | null", "next: string | null",
                               "updated_at: string /* date */ | null"], "source_url": RAMP_URL}


def fake_brex(vendor):
    return FakeDocs(
        lists={"/v2/transactions/card/primary": [BREX_OP]},
        schemas={"Money": {"amount": "integer (int64) — smallest denomination", "currency": "string — ISO 4217"},
                 "CardTransaction": {"id": "string", "description": "string"},
                 "SecurityScopes": {"transactions.card.readonly": "View your card transactions"}},
        searches={"de-duplicate": [{**BREX_OP, "snippet": "De-duplicate the overlapping results by transaction id"}]})


RAMP_JSON = "https://docs.ramp.com/openapi/developer-api.json"
RAMP_MONEY = "https://docs.ramp.com/llms-guides/monetary-values.txt"
RAMP_GUIDES = {
    "smallest denomination": [
        {"title": "monetary-values.txt", "source_url": RAMP_MONEY, "snippet": "integers in the smallest denomination",
         "text": "Most Ramp API monetary values use integers in the smallest denomination of the relevant currency."},
        {"title": "components/schemas/Transaction/properties/entity_amount", "source_url": RAMP_JSON,
         "snippet": "smallest denomination", "text": "The value is in the smallest denomination of the currency."}],
    "deprecated": [{"title": "components/schemas/Transaction/properties/amount", "source_url": RAMP_JSON,
                    "snippet": "Deprecated. Use entity_amount.", "text": "Deprecated. Use `entity_amount`."}],
    # Shares the words "same" and "id" but is about webhook retries: must NOT count as id stability.
    "id": [{"title": "Retry schedule", "source_url": "https://docs.ramp.com/llms-guides/webhooks.txt",
            "snippet": "The same event id is used for all retry attempts",
            "text": "The same event id is used for all retry attempts."}],
}


def fake_ramp(vendor):
    return FakeDocs(guides=RAMP_GUIDES, lists={"/developer/v1/transactions": [RAMP_OP],
                           "/developer/v1/webhooks": [{"title": "Creates a new webhook subscription",
                                                       "path": "/developer/v1/webhooks", "method": "",
                                                       "source_url": RAMP_URL}]})


class _Failing:
    async def __aenter__(self):
        raise McpEvidenceError("ConnectError: All connection attempts failed")

    async def __aexit__(self, *a):
        return None


def failing_session(vendor):
    return _Failing()


def answer(resp, qid):
    return next(a for a in resp["answers"] if a["question_id"] == qid)


def test_brex_money_answer_is_documented_with_evidence():
    resp = asyncio.run(build_vendor_response(req(), BREX, session_factory=fake_brex))
    q3 = answer(resp, "Q3")
    assert q3["finding"] == "documented" and "integer (int64)" in q3["facets"][0]["summary"]
    ev = q3["evidence"][0]
    assert ev["provenance"] == "real_mcp_response" and ev["schema_ref"] == "Money"
    assert ev["source"] == BREX_URL and ev["via"] == VIA          # official source vs our wrapper
    assert validate(resp) == []


def test_missing_doc_is_not_found_not_unsupported():
    resp = asyncio.run(build_vendor_response(req(), BREX, session_factory=fake_brex))
    q6 = answer(resp, "Q6")
    assert q6["finding"] == "not_found_in_reviewed_docs"
    assert q6["answer"] == ("Not found in the reviewed Brex documentation. "
                            "This does not mean the capability is unsupported.")


def test_ramp_amounts_come_from_guides_and_flag_the_deprecated_field():
    resp = asyncio.run(build_vendor_response(req(), RAMP, session_factory=fake_ramp))
    q3 = answer(resp, "Q3")
    assert q3["finding"] == "documented"
    assert {e["source"] for e in q3["evidence"]} == {RAMP_MONEY, RAMP_JSON}
    dep = next(f for f in q3["facets"] if f["kind"] == "deprecation")
    assert dep["found"] and not dep["core"]          # informs the buyer, never decides the finding
    assert validate(resp) == []


def test_unrelated_passage_sharing_words_never_counts_as_id_stability():
    resp = asyncio.run(build_vendor_response(req(), RAMP, session_factory=fake_ramp))
    q2 = answer(resp, "Q2")
    stab = next(f for f in q2["facets"] if f["kind"] == "guarantee")
    assert not stab["found"] and q2["finding"] == "partially_documented"


def test_a_vendor_pattern_alone_never_makes_dedupe_documented():
    resp = asyncio.run(build_vendor_response(req(), BREX, session_factory=fake_brex))
    q5 = answer(resp, "Q5")
    kinds = {f["kind"]: f["found"] for f in q5["facets"]}
    assert kinds["integration_pattern"] is True and kinds["guarantee"] is False
    assert q5["finding"] == "partially_documented"


def test_docs_unavailable_gives_docs_unavailable_not_crash():
    resp = asyncio.run(build_vendor_response(req(), RAMP, session_factory=failing_session))
    assert {a["finding"] for a in resp["answers"]} == {"docs_unavailable"}
    assert any(l.startswith("Live vendor docs unavailable") for l in resp["limitations"])
    assert validate(resp) == []


def test_limitation_copy_and_pitch_label():
    resp = asyncio.run(build_vendor_response(req(), BREX, session_factory=fake_brex))
    assert "Simulated Brex representative operated by the hackathon team, not Brex." in resp["limitations"]
    assert resp["pitch"]["provenance"] == "simulated_vendor_statement"
    assert resp["pitch"]["text"].startswith("(fictional) ")


def test_custom_question_answered_by_keyword_search():
    resp = asyncio.run(build_vendor_response(req("Do you de-duplicate repeated transaction imports?"), BREX,
                                             session_factory=fake_brex))
    qx = answer(resp, "QX")
    assert qx["finding"] == "documented" and "de-duplicate" in qx["answer"]
    nothing = asyncio.run(build_vendor_response(req("Do you support quantum ledgers?"), BREX, session_factory=fake_brex))
    assert answer(nothing, "QX")["finding"] == "not_found_in_reviewed_docs"


def test_search_lookups_are_scoped_to_transaction_endpoints():
    fake = fake_brex("brex")
    asyncio.run(build_vendor_response(req(), BREX, session_factory=lambda v: fake))
    searches = [c["args"] for c in fake.tool_calls if c["tool"] == "search_operations"]
    assert searches and all((a["path_prefix"] or "").startswith("/v2/transactions") for a in searches)


def test_schema_evidence_uses_schema_source():
    class F(FakeDocs):
        async def get_schema(self, name):
            r = await super().get_schema(name)
            if r["schema"]:
                r["schema"]["source_url"] = "https://developer.brex.com/_spec/openapi/expenses_api.yaml"
            return r
    base = fake_brex("brex")
    f = F(lists=base.lists, schemas=base.schemas, searches=base.searches)
    resp = asyncio.run(build_vendor_response(req(), BREX, session_factory=lambda v: f))
    assert answer(resp, "Q2")["evidence"][0]["source"].endswith("expenses_api.yaml")


def test_custom_question_ignores_generic_words_and_needs_all_keywords_in_one_operation():
    # 'repeated', 'transaction', 'imports' are domain stopwords: a hit on 'repeated' alone must not count.
    ramp = FakeDocs(lists=fake_ramp("ramp").lists,
                    searches={"repeated": [{**RAMP_OP, "title": "Upload documents for a vendor agreement",
                                           "path": "/developer/v1/vendors/agreements", "snippet": "repeated"}]})
    resp = asyncio.run(build_vendor_response(req("Do you de-duplicate repeated transaction imports?"), RAMP,
                                             session_factory=lambda v: ramp))
    assert answer(resp, "QX")["finding"] == "not_found_in_reviewed_docs"
    # Two meaningful keywords must co-occur in the same operation.
    opA = {**RAMP_OP, "title": "A", "path": "/a", "snippet": "agreement"}
    opB = {**RAMP_OP, "title": "B", "path": "/b", "snippet": "renewal"}
    split = FakeDocs(searches={"agreement": [opA], "renewal": [opB]})
    together = FakeDocs(searches={"agreement": [opA], "renewal": [opA]})
    q = req("Is there an agreement renewal?")
    assert answer(asyncio.run(build_vendor_response(q, RAMP, session_factory=lambda v: split)), "QX")["finding"] == "not_found_in_reviewed_docs"
    assert answer(asyncio.run(build_vendor_response(q, RAMP, session_factory=lambda v: together)), "QX")["finding"] == "documented"
