from judas.contracts import (BUYER_REQUIREMENTS, COMPARISON, ERROR, VENDOR_RESPONSE, envelope,
                             error_payload, validate)
from judas.questions import QUESTIONS, questions_payload


def _buyer():
    return {"name": "Fictional Northstar Finance", "employees": 300, "synthetic": True}


def test_envelope_ids_and_roundtrip():
    p = envelope(BUYER_REQUIREMENTS, "eval-1", buyer=_buyer(), request="r", questions=questions_payload())
    assert p["message_id"].startswith("jm-") and p["request_id"].startswith("req-")
    assert validate(p) == []


def test_custom_question_allowed_and_bounded():
    ok = envelope(BUYER_REQUIREMENTS, "eval-1", buyer=_buyer(), request="r", questions=questions_payload(),
                  custom_question="Do you support multi-entity expense sync?")
    assert validate(ok) == []
    too_long = dict(ok, custom_question="x" * 301)
    assert any("custom_question" in e for e in validate(too_long))


def test_documented_without_mcp_evidence_is_invalid():
    p = envelope(VENDOR_RESPONSE, "eval-1", "brex",
                 answers=[{"question_id": "Q3", "finding": "documented", "answer": "a", "evidence": []}])
    assert any("evidence" in e for e in validate(p))


def test_amended_findings_only():
    p = envelope(VENDOR_RESPONSE, "eval-1", "brex",
                 answers=[{"question_id": "Q6", "finding": "not_documented", "answer": "a", "evidence": []}])
    assert any("finding" in e for e in validate(p))
    ok = envelope(VENDOR_RESPONSE, "eval-1", "brex",
                  answers=[{"question_id": "Q6", "finding": "not_found_in_reviewed_docs", "answer": "a", "evidence": []}])
    assert validate(ok) == []


def test_unknown_schema_and_bad_verdict():
    assert validate({"schema": "nope/v1"})
    c = envelope(COMPARISON, "eval-1", rows=[], coverage={}, verdict="leans:ramp", caveat="c")
    assert any("verdict" in e for e in validate(c))
    good = envelope(COMPARISON, "eval-1", rows=[], coverage={}, verdict="docs_favor:brex", caveat="c")
    assert validate(good) == []


def test_error_payload():
    e = error_payload("eval-1", "boom")
    assert e["schema"] == ERROR and e["message"] == "boom" and validate(e) == []


def test_question_set():
    assert [q.id for q in QUESTIONS] == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]
    assert {q.id for q in QUESTIONS if q.must_have} == {"Q1", "Q2", "Q3", "Q5"}
    assert questions_payload()[2] == {"id": "Q3", "requirement": "amounts & currency",
                                      "ask": "How are amounts and currency represented?"}


def test_validate_tolerates_malformed_items_without_raising():
    p = envelope(VENDOR_RESPONSE, "eval-1", "brex", answers=[None, "x", {"evidence": ["str", None]}, {"finding": 7}])
    problems = validate(p)
    assert problems and all(isinstance(x, str) for x in problems)
    p2 = envelope(VENDOR_RESPONSE, "eval-1", "brex", answers="not-a-list")
    assert validate(p2)
