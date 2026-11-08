from pathlib import Path

import pytest
from starlette.testclient import TestClient

from judas import evaluation
from tests.test_evaluation import FakeSellers
from web import events

ROOT = Path(__file__).resolve().parents[1]
LABELS = ["Project Judas", "Expense sync: Ramp vs Brex", "Discover agents", "Contact Ramp", "Contact Brex",
          "Evaluate both", "Vendor comparison", "Interaction history"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "EVALUATIONS_DIR", tmp_path / "evaluations")
    monkeypatch.setattr(events, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(evaluation, "send_to_seller", FakeSellers())
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    from web.app import build_app
    with TestClient(build_app()) as c:
        yield c


def test_scenario_and_page(client):
    s = client.get("/api/judas/scenario").json()
    assert s["label"] == "Expense sync: Ramp vs Brex" and len(s["requirements"]) == 7
    html = client.get("/judas").text
    for label in LABELS:
        assert label in html, label


@pytest.mark.parametrize("body", [{"request": ""}, {"request": "   "}, {"request": "x" * 2001},
                                  {"request": "ok", "custom_question": "y" * 301}, {}])
def test_discover_rejects_bad_request(client, body):
    assert client.post("/api/judas/discover", json=body).status_code == 400


def test_full_flow(client):
    d = client.post("/api/judas/discover", json={"request": "<script>alert(1)</script> sync expenses",
                                                 "custom_question": "Do you de-duplicate imports?"}).json()
    assert {a["vendor"] for a in d["agents"]} == {"ramp", "brex"}
    eid = d["evaluation_id"]
    r = client.post(f"/api/judas/evaluations/{eid}/contact", json={"vendor": "ramp"})
    assert r.status_code == 200 and r.json()["response"]["vendor_id"] == "ramp"
    ev = client.post(f"/api/judas/evaluations/{eid}/evaluate").json()
    assert ev["comparison"]["caveat"].startswith("Based on reviewed official documentation")
    assert ev["requirements"]["request"].startswith("<script>")      # stored verbatim; UI renders as text
    assert ev["requirements"]["custom_question"] == "Do you de-duplicate imports?"
    assert client.get(f"/api/judas/evaluations/{eid}").json()["comparison"]["verdict"]


def test_bad_vendor_and_unknown_eval(client):
    eid = client.post("/api/judas/discover", json={"request": "x"}).json()["evaluation_id"]
    assert client.post(f"/api/judas/evaluations/{eid}/contact", json={"vendor": "acme"}).status_code == 400
    assert client.get("/api/judas/evaluations/eval-20990101-abcdef").status_code == 404
    assert client.post("/api/judas/evaluations/..%2f..%2fetc/evaluate").status_code == 404


def test_two_evaluations_stay_separate(client):
    a = client.post("/api/judas/discover", json={"request": "x"}).json()["evaluation_id"]
    b = client.post("/api/judas/discover", json={"request": "y"}).json()["evaluation_id"]
    client.post(f"/api/judas/evaluations/{a}/evaluate"); client.post(f"/api/judas/evaluations/{b}/evaluate")
    ea = client.get(f"/api/judas/evaluations/{a}/events").json()["events"]
    assert a != b and ea and all(e["session_id"] == a for e in ea)


def test_reset_archives_not_deletes(client, tmp_path):
    client.post("/api/judas/discover", json={"request": "x"})
    r = client.post("/api/judas/reset").json()
    assert r["archived"] and Path(r["archived"]).exists()


def test_judas_js_never_uses_innerhtml():
    js = (ROOT / "web" / "static" / "judas.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js


def test_judas_page_loads_its_script_and_styles(client):
    html = client.get("/judas").text
    assert '/static/judas.js' in html and '/static/styles.css' in html
    assert 'id="custom-question"' in html and 'id="comparison"' in html and 'id="history"' in html


def test_judas_js_renders_through_null_safe_fill():
    # replaceChildren(null) inserts the text "null"; only fill() may call it (it filters nulls).
    js = (ROOT / "web" / "static" / "judas.js").read_text()
    assert js.count(".replaceChildren(") == 1 and "filter((k) => k != null)" in js


def test_same_evaluation_concurrent_contacts_both_persist(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from tests.test_evaluation import SlowSellers
    monkeypatch.setattr(evaluation, "send_to_seller", SlowSellers(0.4))
    eid = client.post("/api/judas/discover", json={"request": "x"}).json()["evaluation_id"]
    with ThreadPoolExecutor(2) as pool:
        futs = [pool.submit(client.post, f"/api/judas/evaluations/{eid}/contact", json={"vendor": v})
                for v in ("ramp", "brex")]
        assert all(f.result().status_code == 200 for f in futs)
    ev = client.get(f"/api/judas/evaluations/{eid}").json()
    assert ev["responses"].get("ramp") and ev["responses"].get("brex")      # neither write was lost
    kinds = [e["event_type"] for e in client.get(f"/api/judas/evaluations/{eid}/events").json()["events"]]
    assert kinds.count("vendor_contacted") == 2


def test_judas_js_has_single_inflight_guard():
    js = (ROOT / "web" / "static" / "judas.js").read_text()
    assert "state.inflight" in js and js.index("state.inflight") < js.index("async function discover")
