"""Unit tests for the Crafting evaluation contract. MOCKED: no Crafting sandbox and no vendor is contacted here.
The live runs are recorded separately in data/crafting/runs/*.json (see crafting/README.md)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "crafting" / "service"))

from crafting import judas_bridge, schema  # noqa: E402
import vendors as V  # noqa: E402


def _test(name, status="passed", level=1):
    return {"name": name, "level": level, "operation": "op", "responsibility": "vendor", "verification_method": "m",
            "input": {}, "started_at": "t", "duration_ms": 1, "vendor_resource_id": "r1", "readback_verified": True,
            "expected": {}, "observed": {}, "status": status, "error": None}


def _ev(tests, status="completed"):
    return {"schema": schema.SCHEMA, "vendor": "airtable", "scenario": "s", "requirement": "r", "sandbox": "sb", "org": "demo",
            "template": "t", "test_run_id": "x", "environment": "crafting", "execution_status": status, "started_at": "a",
            "finished_at": "b", "tests": tests, "summary": schema.summarize(tests), "provenance": {}, "execution_log": []}


def test_valid_evidence_has_no_problems():
    assert schema.validate(_ev([_test("a"), _test("b", "blocked", 3)])) == []


def test_validation_catches_missing_fields_and_bad_status():
    ev = _ev([{**_test("a"), "status": "maybe"}])
    del ev["provenance"]
    probs = schema.validate(ev)
    assert any("provenance" in p for p in probs) and any("bad status" in p for p in probs)


def test_completed_run_without_tests_is_invalid():
    assert "completed run has no tests" in schema.validate(_ev([]))


def test_summary_counts_by_level():
    s = schema.summarize([_test("a", "passed", 1), _test("b", "failed", 3), _test("c", "blocked", 3)])
    assert (s["passed"], s["failed"], s["blocked"]) == (1, 1, 1)
    assert s["by_level"]["3"] == {"passed": 0, "failed": 1, "blocked": 1}


def test_bridge_groups_pass_fail_and_blocked_is_not_a_failure():
    by = {n: _test(n) for n in judas_bridge.GROUPS["status_sync"]}
    assert judas_bridge.group_status(by, judas_bridge.GROUPS["status_sync"])[0] == "pass"
    by["readback_updated_status"] = _test("readback_updated_status", "failed")
    assert judas_bridge.group_status(by, judas_bridge.GROUPS["status_sync"])[0] == "fail"
    by["readback_updated_status"] = _test("readback_updated_status", "blocked")
    assert judas_bridge.group_status(by, judas_bridge.GROUPS["status_sync"])[0] == "skipped"
    assert judas_bridge.group_status({}, ["x"])[0] == "skipped"


def test_vendor_http_errors_are_classified(monkeypatch):
    import io
    import urllib.error

    def boom(code):
        def f(*a, **k):
            raise urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(b"{}"))
        return f
    for code, cls in ((401, V.BlockedError), (403, V.BlockedError), (429, V.BlockedError), (503, V.TransientError),
                      (422, V.VendorError)):
        monkeypatch.setattr(V.urllib.request, "urlopen", boom(code))
        try:
            V._http("GET", "https://example.invalid", {})
        except V.VendorError as e:
            assert type(e) is cls, code


def test_missing_credential_is_blocked_not_failed(monkeypatch, tmp_path):
    monkeypatch.setitem(V.CRED_PATHS, "airtable", str(tmp_path / "nope"))
    try:
        V._token("airtable")
        raise AssertionError("expected BlockedError")
    except V.BlockedError:
        pass
