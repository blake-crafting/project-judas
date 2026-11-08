import copy

from judas.compare import CAVEAT, compare
from judas.contracts import VENDOR_RESPONSE, envelope, validate

RAMP_URL = "https://docs.ramp.com/llms-api.txt"
BREX_URL = "https://developer.brex.com/_spec/openapi/transactions_api.yaml"
VIA = "hackathon-docs MCP (our wrapper, not the vendor's)"


def _ev(source, op=None, schema=None, fields=None, tool="list_operations"):
    return {"provenance": "real_mcp_response", "tool": tool, "source": source, "via": VIA, "operation": op,
            "schema_ref": schema, "fields": fields or {}, "retrieved_at": "2026-11-07T00:00:00+00:00"}


def _resp(vendor, findings, evidence):
    answers = []
    for qid in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]:
        f = findings.get(qid, "documented")
        answers.append({"question_id": qid, "finding": f, "answer": f"{vendor} {qid} answer",
                        "evidence": [evidence.get(qid, _ev(RAMP_URL if vendor == "ramp" else BREX_URL, op="/x"))]
                        if f == "documented" else []})
    return envelope(VENDOR_RESPONSE, "e", vendor, answers=answers, limitations=[])


RAMP_OK = _resp("ramp", {"Q7": "not_found_in_reviewed_docs"}, {
    "Q3": _ev(RAMP_URL, op="/developer/v1/transactions",
              fields={"amount": "amount?: number", "currency_code": "currency_code?: string",
                      "minor_unit_conversion_rate": "minor_unit_conversion_rate: number | null"})})
BREX_OK = _resp("brex", {"Q6": "not_found_in_reviewed_docs"}, {
    "Q3": _ev(BREX_URL, schema="Money", tool="get_schema",
              fields={"amount": "integer (int64) — smallest denomination", "currency": "string — ISO 4217"})})


def with_findings(resp, **overrides):
    r = copy.deepcopy(resp)
    for a in r["answers"]:
        if a["question_id"] in overrides:
            a["finding"] = overrides[a["question_id"]]
            if a["finding"] != "documented":
                a["evidence"] = []
    return r


def row(c, qid):
    return next(r for r in c["rows"] if r["question_id"] == qid)


def test_verdict_insufficient_when_vendor_missing():
    assert compare("e", {"ramp": RAMP_OK, "brex": None})["verdict"] == "insufficient_evidence"


def test_must_have_difference_favors_documenting_vendor():
    c = compare("e", {"ramp": with_findings(RAMP_OK, Q5="not_found_in_reviewed_docs"), "brex": BREX_OK})
    assert c["verdict"] == "docs_favor:brex" and "Q5" in c["verdict_reason"]


def test_no_clear_difference_when_balanced():
    assert compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})["verdict"] == "no_clear_difference_in_docs"


def test_q3_has_no_automatic_clarification_and_caveat_names_both_evidence_kinds():
    # The old rule asked Ramp to clarify `amount` because only the deprecated field was reviewed.
    c = compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})
    assert row(c, "Q3")["ramp"]["status"] == "documented" and not c["clarifications"]
    assert c["caveat"] == CAVEAT and "synthetic" in CAVEAT and "does not mean a capability is unsupported" in CAVEAT


def test_cells_are_untested_and_keep_official_source_separate():
    c = compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})
    cells = [r[v] for r in c["rows"] for v in ("ramp", "brex")]
    assert all(cell["tested"] is False for cell in cells)
    q3b = row(c, "Q3")["brex"]
    assert q3b["source"] == BREX_URL and q3b["via"] == VIA


def test_not_found_is_not_called_unsupported():
    c = compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})
    q6 = row(c, "Q6")
    assert q6["brex"]["status"] == "not_found_in_reviewed_docs"
    assert "unsupported" not in (q6["buyer_note"] or "").lower()


def test_coverage_counts_documented():
    c = compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})
    assert c["coverage"] == {"ramp": "6/7", "brex": "6/7"}


def test_deterministic():
    a = compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})
    b = compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})
    for x in (a, b):
        x.pop("message_id"); x.pop("request_id")
    assert a == b


def test_unsubstantiated_documented_claim_is_never_counted():
    forged = copy.deepcopy(BREX_OK)
    for a in forged["answers"]:
        a["finding"], a["evidence"] = "documented", []   # says 'documented' for all 7, shows nothing
    c = compare("e", {"ramp": RAMP_OK, "brex": forged})
    assert c["coverage"]["brex"] == "0/7"
    assert all(r["brex"]["status"] == "docs_unavailable" for r in c["rows"])
    assert row(c, "Q1")["brex"]["summary"].startswith("Unsubstantiated claim")
    # Every Brex claim is unverifiable, so the buyer refuses to compare rather than crowning Ramp.
    assert c["verdict"] == "insufficient_evidence" and "Brex" in c["verdict_reason"]


def _partial_brex():
    """Brex answered Q1–Q2, then its docs session died (a designed path): Q3–Q7 docs_unavailable."""
    r = copy.deepcopy(BREX_OK)
    for a in r["answers"]:
        if a["question_id"] not in ("Q1", "Q2"):
            a["finding"], a["evidence"] = "docs_unavailable", []
    return r


def test_unavailable_docs_never_become_one_sided_findings():
    c = compare("e", {"ramp": RAMP_OK, "brex": _partial_brex()})
    assert c["verdict"] == "insufficient_evidence" and "unavailable" in c["verdict_reason"].lower()
    for r in c["rows"]:
        if r["brex"]["finding"] == "docs_unavailable":
            assert r["buyer_note"] is None, r["question_id"]
    down = compare("e", {"ramp": RAMP_OK, "brex": None})
    assert all(r["buyer_note"] is None for r in down["rows"])


def test_evidence_source_must_be_the_vendors_official_doc_url():
    forged = copy.deepcopy(BREX_OK)
    for a in forged["answers"]:
        for e in a["evidence"]:
            e["source"] = "https://evil.example/not-brex.yaml"
    c = compare("e", {"ramp": RAMP_OK, "brex": forged})
    assert c["coverage"]["brex"] == "0/7"
    assert row(c, "Q3")["brex"]["source"] is None


def test_rows_mark_must_haves_and_the_rows_that_decide_the_verdict():
    # The page highlights the rows a verdict rests on, so the buyer can see why it came out that way.
    c = compare("e", {"ramp": with_findings(RAMP_OK, Q5="not_found_in_reviewed_docs"), "brex": BREX_OK})
    assert [r["question_id"] for r in c["rows"] if r["must_have"]] == ["Q1", "Q2", "Q3", "Q5"]
    assert c["deciding"] == ["Q5"]


def test_no_deciding_rows_without_a_one_sided_must_have():
    assert compare("e", {"ramp": RAMP_OK, "brex": BREX_OK})["deciding"] == []
    assert compare("e", {"ramp": RAMP_OK, "brex": None})["deciding"] == []


def test_partial_vs_partial_on_a_must_have_never_decides_and_asks_for_testing():
    both = {"ramp": with_findings(RAMP_OK, Q5="partially_documented"),
            "brex": with_findings(BREX_OK, Q5="partially_documented")}
    for r in both.values():                       # partial answers carry evidence
        next(a for a in r["answers"] if a["question_id"] == "Q5")["evidence"] = [
            _ev(RAMP_URL if r["vendor_id"] == "ramp" else BREX_URL, op="/x")]
    c = compare("e", both)
    assert c["verdict"] == "additional_testing_required" and c["deciding"] == []
    assert [u["question_id"] for u in c["explanation"]["unresolved"]] == ["Q5"]


def test_documented_vs_partial_on_a_must_have_decides():
    brex = with_findings(BREX_OK, Q6="partially_documented")
    next(a for a in brex["answers"] if a["question_id"] == "Q6")["evidence"] = [_ev(BREX_URL, op="/x")]
    ramp = with_findings(RAMP_OK, Q6="documented")
    realtime = {"Q6": "must"}
    assert compare("e", {"ramp": ramp, "brex": brex})["verdict"] != "docs_favor:ramp"        # Q6 nice by default
    c = compare("e", {"ramp": ramp, "brex": brex}, priorities=realtime)
    assert c["verdict"] == "docs_favor:ramp" and c["deciding"] == ["Q6"]


def test_skipped_requirements_are_not_compared():
    c = compare("e", {"ramp": RAMP_OK, "brex": BREX_OK}, priorities={"Q6": "skip", "Q7": "skip"})
    assert [r["question_id"] for r in c["rows"]] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert c["coverage_detail"]["ramp"]["of"] == 5


def test_synthetic_demo_raises_evidence_level_but_never_the_finding():
    demo = {"schema": "judas.demo_result/v1", "observations": [{"requirements": ["Q5"], "text": "dedup kept 6"}]}
    ramp = with_findings(RAMP_OK, Q5="not_found_in_reviewed_docs")
    c = compare("e", {"ramp": ramp, "brex": BREX_OK}, demos={"ramp": demo})
    cell = row(c, "Q5")["ramp"]
    assert cell["finding"] == "not_found_in_reviewed_docs"
    assert cell["evidence_levels"] == ["synthetic_demo"] and cell["observed"] == ["dedup kept 6"]


def test_demo_result_cannot_claim_vendor_evidence_without_a_vendor_hosted_endpoint():
    base = envelope("judas.demo_result/v1", "e", "ramp", demo="card_transaction_sync",
                    endpoint={"url": "http://127.0.0.1:9105/mcp", "vendor_hosted": False},
                    tool_calls=[{"tool": "list_card_transactions"}], observations=[], provenance="synthetic_fixture")
    assert validate({**base, "evidence_level": "synthetic_demo"}) == []
    assert validate({**base, "evidence_level": "vendor_demo"})            # rejected
    assert validate({**base, "evidence_level": "synthetic_demo", "provenance": "real_mcp_response"})
    assert validate({**base, "evidence_level": "synthetic_demo", "tool_calls": []})   # nothing executed
