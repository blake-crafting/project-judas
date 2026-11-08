"""Web API tests: Workflow 1 through the same HTTP API the browser uses.

Real seller processes are started on test ports (Ramp adapter in fixture mode
for determinism, plus the fictional demo sellers). A temp registry and a temp
event log keep tests away from the developer's running demo.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SELLER_PORT, DEMO_PORT, DEAD_PORT = 9188, 9189, 9187


def _spawn(module: str, env: dict, probe: str):
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
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("web")
    reg = json.loads((ROOT / "buyer" / "registry.json").read_text())
    urls = {"ramp-docs-adapter": f"http://127.0.0.1:{SELLER_PORT}",
            "ledgerleaf-demo": f"http://127.0.0.1:{DEMO_PORT}/ledgerleaf",
            "payroll-pal-demo": f"http://127.0.0.1:{DEAD_PORT}/payrollpal"}  # nothing listens here
    for a in reg["agents"]:
        a["base_url"] = urls.get(a["id"], f"http://127.0.0.1:{DEAD_PORT}")
    (tmp / "registry.json").write_text(json.dumps(reg))

    procs = [
        _spawn("seller.server", {"SELLER_PORT": str(SELLER_PORT), "EVIDENCE_SOURCE": "off"},
               f"http://127.0.0.1:{SELLER_PORT}/.well-known/agent-card.json"),
        _spawn("seller.demo_sellers", {"DEMO_SELLERS_PORT": str(DEMO_PORT)},
               f"http://127.0.0.1:{DEMO_PORT}/ledgerleaf/.well-known/agent-card.json"),
    ]
    os.environ["REGISTRY_PATH"] = str(tmp / "registry.json")
    from web import app as webapp, events
    events.EVENTS_PATH = tmp / "events.jsonl"
    with TestClient(webapp.build_app()) as c:
        yield c
    os.environ.pop("REGISTRY_PATH", None)
    for p in procs:
        p.terminate(); p.wait()


SAMPLE = {"question": "Does Ramp expose API operations for expense and vendor records?",
          "requirements": ["vendor records", "expenses", "API documentation"], "consent_to_share": True}


def test_page_and_scenarios_served(client):
    assert "Discover agents" in client.get("/").text
    labels = [s["label"] for s in client.get("/api/scenarios").json()["scenarios"]]
    assert "Software procurement API" in labels


@pytest.mark.parametrize("body,msg", [
    ({"question": "", "requirements": []}, "Type a question"),
    ({**SAMPLE, "consent_to_share": False}, "not consented"),
    ({"question": "x", "requirements": 5}, "requirements must be a list"),
])
def test_discover_rejects_bad_input(client, body, msg):
    r = client.post("/api/discover", json=body)
    assert r.status_code == 400 and msg in r.json()["error"]


def test_workflow1_discover_then_ask_records_full_event_trail(client):
    d = client.post("/api/discover", json=SAMPLE).json()
    sid = d["session_id"]
    assert d["results"][0]["id"] == "ramp-docs-adapter"

    r = client.post("/api/ask", json={"session_id": sid, "agent_id": "ramp-docs-adapter",
                                      "requirement": d["requirement"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["response"]["request_id"] == d["requirement"]["request_id"]
    assert body["response"]["evidence_mode"] == "local_fixture"  # seller started with EVIDENCE_SOURCE=off
    types = [e["event_type"] for e in body["events"]]
    for t in ["buyer_requirement_submitted", "registry_searched", "agent_discovered", "seller_connected",
              "question_sent", "seller_answer_received", "evidence_unavailable", "evaluation_completed"]:
        assert t in types, t
    assert all(e["session_id"] == sid and e["timestamp"] and e["event_id"] for e in body["events"])
    sent = next(e for e in body["events"] if e["event_type"] == "question_sent")
    assert sent["payload"]["payload"]["question"] == SAMPLE["question"]  # exactly what the buyer sent


def test_answers_differ_by_vendor(client):
    d = client.post("/api/discover", json={"question": "bills, webhooks, payroll?", "requirements": []}).json()
    ramp = client.post("/api/ask", json={"session_id": d["session_id"], "agent_id": "ramp-docs-adapter",
                                         "requirement": d["requirement"]}).json()["response"]
    leaf = client.post("/api/ask", json={"session_id": d["session_id"], "agent_id": "ledgerleaf-demo",
                                         "requirement": d["requirement"]}).json()["response"]
    assert leaf["evidence_mode"] == "demo_profile"
    assert {c["requirement"]: c["finding"] for c in leaf["claims"]}["webhooks"] == "unsupported"
    assert ramp["claims"] != leaf["claims"]


def test_unknown_agent_rejected(client):
    r = client.post("/api/ask", json={"agent_id": "http://169.254.169.254", "requirement": SAMPLE})
    assert r.status_code == 400 and "Only registry agents" in r.json()["error"]


def test_unreachable_seller_gives_actionable_error_and_logs_it(client):
    r = client.post("/api/ask", json={"session_id": "sess-down", "agent_id": "payroll-pal-demo", "requirement": SAMPLE})
    assert r.status_code == 502
    assert "./dev.sh" in r.json()["hint"]
    evs = client.get("/api/events", params={"session_id": "sess-down"}).json()["events"]
    assert [e["event_type"] for e in evs] == ["exchange_failed"]


def test_reset_clears_log(client):
    assert client.get("/api/events").json()["events"]
    client.post("/api/reset", json={})
    assert client.get("/api/events").json()["events"] == []
