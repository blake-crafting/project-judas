"""Live real-vendor scenario: contract rules, buyer-side readback checks, evidence levels, verdict.
All tests here use mocked data; the live end-to-end run is a separate, labeled script."""

from judas import live
from judas.contracts import envelope, validate

ALL = {"get_board_info", "create_group", "create_item", "change_item_column_values"}


def _result(vendor="monday", url="https://mcp.monday.com/mcp", tools=ALL, steps=None, vendor_operated=True):
    steps = steps if steps is not None else [{"test": "A2", "tool": "create_item", "ok": True}]
    return envelope("judas.operation_result/v1", "eval-t", vendor,
                    interface={"kind": "official_vendor_mcp", "url": url, "vendor_operated": vendor_operated},
                    tools_available=sorted(tools), steps=steps, created={"container_id": "1"})


def test_operation_result_contract_rejects_false_vendor_claims_and_unlisted_tools():
    assert validate(_result()) == []
    assert validate(_result(url="http://127.0.0.1:9105/mcp"))            # our sandbox is not vendor-operated
    assert validate(_result(vendor="airtable"))                          # monday host claimed for Airtable
    assert validate(_result(steps=[{"test": "A2", "tool": "delete_everything", "ok": True}]))
    assert validate(_result(url="http://127.0.0.1:9105/mcp", vendor_operated=False)) == []


def _actual(override=None):
    a = {k: dict(v) for k, v in live.expected_state().items()}
    for (name, field), val in (override or {}).items():
        a.setdefault(name, {})[field] = val
    return a


def test_readback_checks_pass_only_on_exact_persisted_values():
    assert all(c["pass"] for c in live.check_readback(_actual(), True))
    checks = live.check_readback(_actual({(live.SCENARIO["update"]["name"], "Status"): "To do"}), True)
    bad = [c for c in checks if not c["pass"]]
    assert len(bad) == 1 and bad[0]["test"] == "A3"                      # the update did not persist
    assert not live.check_readback(_actual(), False)[0]["pass"]           # tasks not inside the container


def test_evidence_levels():
    ok = live.evidence(_result(), None, live.check_readback(_actual(), True), ALL)
    assert {k: v["level"] for k, v in ok.items()} == {"A1": "verified", "A2": "verified", "A3": "verified",
                                                      "A4": "tool_available"}
    blocked = live.evidence(None, "monday is not configured for live calls", [], ALL)
    assert {v["level"] for v in blocked.values()} == {"blocked"}
    unchecked = live.evidence(_result(), None, [], ALL)
    assert unchecked["A2"]["level"] == "executed"                        # seller said so; buyer could not verify


def test_verdict_separates_proven_failed_and_blocked():
    good = live.evidence(_result(), None, live.check_readback(_actual(), True), ALL)
    failed = live.evidence(_result(), None, live.check_readback(_actual({(live.SCENARIO["update"]["name"], "Status"): "To do"}), True), ALL)
    blocked = live.evidence(None, "not configured", [], ALL)
    assert live.verdict({"airtable": good, "monday": good})[0] == "both_proven"
    assert live.verdict({"airtable": good, "monday": failed})[0] == "live_favor:airtable"
    assert live.verdict({"airtable": good, "monday": blocked})[0] == "insufficient_evidence"


def test_an_operation_error_is_not_a_vendor_failure_and_never_hands_the_other_vendor_a_win():
    # Found live: our adapter sent a number where monday's tool wanted a string. That is neither a missing
    # prerequisite ("blocked") nor proof the product can't do it ("failed").
    good = live.evidence(_result(), None, live.check_readback(_actual(), True), ALL)
    err = live.evidence(None, "RuntimeError: monday.com operation failed: create_group failed: invalid arguments", [], ALL)
    assert {v["level"] for v in err.values()} == {"unknown"}
    assert all("operation failed" in v["detail"] for v in err.values())
    assert live.verdict({"airtable": good, "monday": err})[0] == "insufficient_evidence"


def test_live_page_and_scenario_api(monkeypatch):
    from starlette.testclient import TestClient
    import vendors_live.config as cfg
    from web.app import app
    monkeypatch.setattr(cfg, "status", lambda: {"airtable": True, "monday": False})
    c = TestClient(app)
    html = c.get("/judas/live").text
    assert "/static/judas_live.js" in html and "Run live evaluation" in html and "not operated by Airtable or monday.com" in html
    d = c.get("/api/live/scenario").json()
    assert [t["id"] for t in d["tests"]] == ["A1", "A2", "A3", "A4"]
    assert {a["id"] for a in d["agents"]} == {"airtable-live-adapter", "monday-live-adapter"}
    assert {v["id"]: v["native_a2a"] is not None for v in d["vendors"]} == {"airtable": False, "monday": True}


def test_live_js_builds_nodes_safely_and_ramp_brex_discovery_is_unchanged():
    from pathlib import Path
    from buyer.client import load_registry
    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "judas_live.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js
    sellers = [a["id"] for a in load_registry() if "vendor_seller" in a.get("roles", [])]
    assert sellers == ["ramp-docs-adapter", "brex-docs-adapter"]       # live adapters have their own role


def test_crafting_evidence_is_verified_only_with_independent_readback_and_not_run_is_honest():
    assert {e["level"] for e in live.crafting_evidence({"status": "not_run", "reason": "runner not configured"}).values()} == {"not_run"}
    ran = {"status": "ran", "result": {"tests": [{"id": "incident_ingest", "status": "pass"},
                                                 {"id": "repeated_events", "status": "pass"},
                                                 {"id": "status_sync", "status": "fail", "detail": "still Open"}]},
           "judas_readback": {"found": True, "status_ok": False}}
    ev = live.crafting_evidence(ran)
    assert ev["C1"]["level"] == "verified" and ev["C2"]["level"] == "verified" and ev["C3"]["level"] == "failed"
    unconfirmed = live.crafting_evidence({**ran, "judas_readback": {"found": False}})
    assert unconfirmed["C1"]["level"] == "executed"                     # runner said pass; Judas couldn't confirm
    assert live.compat_verdict({"airtable": ev, "monday": live.crafting_evidence({"status": "not_run"})})[0] == "compat_incomplete"
    assert live.compat_verdict({v: live.crafting_evidence(None) for v in live.VENDORS})[0] == "not_run"


def test_skipped_crafting_tests_are_not_run_and_never_decide_compatibility():
    # Found live: the bridge skipped every monday test (not implemented yet). That is "not run", not 0/3.
    skipped = {"status": "ran", "result": {"tests": [{"id": t["runner_id"], "status": "skipped"} for t in live.CRAFTING_TESTS]},
               "judas_readback": {"found": False}}
    ok = {"status": "ran", "result": {"tests": [{"id": t["runner_id"], "status": "pass"} for t in live.CRAFTING_TESTS]},
          "judas_readback": {"found": True, "status_ok": True}}
    assert {e["level"] for e in live.crafting_evidence(skipped).values()} == {"not_run"}
    code, reason = live.compat_verdict({"airtable": live.crafting_evidence(ok), "monday": live.crafting_evidence(skipped)})
    assert code == "compat_incomplete" and "monday.com" in reason


def _store(tmp_path, monkeypatch, access="old-access", refresh="refresh-1"):
    import json
    from vendors_live import oauth
    monkeypatch.setattr(oauth, "STORE", tmp_path)
    (tmp_path / "airtable.json").write_text(json.dumps({"tokens": {"access_token": access, "token_type": "Bearer",
                                                                    "refresh_token": refresh},
                                                         "client": {"client_id": "client-123"}}))
    return oauth


def test_expired_airtable_login_is_renewed_before_use(tmp_path, monkeypatch):
    # Found live: the hourly-expiring login made the first run after expiry fail ("OAuth login required").
    import asyncio, json
    import httpx2
    oauth = _store(tmp_path, monkeypatch)
    seen = []

    def handler(req):
        seen.append((req.method, str(req.url)))
        if req.url.path.endswith("/whoami"):
            return httpx2.Response(401 if req.headers["Authorization"] == "Bearer old-access" else 200, json={})
        body = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        assert body["grant_type"] == "refresh_token" and body["refresh_token"] == "refresh-1" and body["client_id"] == "client-123"
        return httpx2.Response(200, json={"access_token": "new-access", "refresh_token": "refresh-2", "expires_in": 3600})
    renewed = asyncio.run(oauth.ensure_fresh("airtable", transport=httpx2.MockTransport(handler)))
    saved = json.loads((tmp_path / "airtable.json").read_text())
    assert renewed is True and saved["tokens"]["access_token"] == "new-access" and saved["tokens"]["refresh_token"] == "refresh-2"
    assert saved["client"]["client_id"] == "client-123"                      # nothing else in the file is lost
    assert oct((tmp_path / "airtable.json").stat().st_mode & 0o777) == "0o600"


def test_working_airtable_login_is_left_alone_and_failed_renewal_is_reported(tmp_path, monkeypatch):
    import asyncio
    import httpx2
    import pytest
    oauth = _store(tmp_path, monkeypatch)
    ok = httpx2.MockTransport(lambda req: httpx2.Response(200, json={}))
    assert asyncio.run(oauth.ensure_fresh("airtable", transport=ok)) is False   # one whoami call, no renewal
    bad = httpx2.MockTransport(lambda req: httpx2.Response(401 if "whoami" in str(req.url) else 400, json={}))
    with pytest.raises(RuntimeError, match="renewal failed"):
        asyncio.run(oauth.ensure_fresh("airtable", transport=bad))
