import asyncio

import pytest

from judas import evaluation
from judas.contracts import VENDOR_RESPONSE, envelope
from web import events


def _vendor_response(eid, vendor):
    ans = [{"question_id": f"Q{i}", "finding": "documented", "answer": f"{vendor} Q{i}",
            "evidence": [{"provenance": "real_mcp_response", "source": "https://x", "via": "mcp",
                          "operation": "/x", "fields": {}}]} for i in range(1, 8)]
    return envelope(VENDOR_RESPONSE, eid, vendor, answers=ans, limitations=[])


class FakeSellers:
    def __init__(self, down=(), hang=()):
        self.calls, self.down, self.hang = [], set(down), set(hang)

    async def __call__(self, agent_id, payload, context_id=None, agents=None):
        self.calls.append({"agent_id": agent_id, "context_id": context_id, "payload": payload})
        vendor = "brex" if "brex" in agent_id else "ramp"
        if vendor in self.down:
            raise ConnectionError("All connection attempts failed")
        if vendor in self.hang:
            await asyncio.sleep(5)
        return {"agent": {"id": agent_id}, "response": _vendor_response(context_id, vendor),
                "trace": [{"step": "seller_answer_received", "state": "TASK_STATE_COMPLETED"}]}


@pytest.fixture
def tmp_eval_env(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "EVALUATIONS_DIR", tmp_path / "evaluations")
    monkeypatch.setattr(events, "EVENTS_PATH", tmp_path / "events.jsonl")
    return tmp_path


def _use(monkeypatch, fake):
    monkeypatch.setattr(evaluation, "send_to_seller", fake)
    return fake


def test_evaluate_both_and_persist(tmp_eval_env, monkeypatch):
    _use(monkeypatch, FakeSellers())
    ev = evaluation.new_evaluation(evaluation.DEFAULT_REQUEST)
    asyncio.run(evaluation.discover(ev)); asyncio.run(evaluation.evaluate(ev)); evaluation.save(ev)
    loaded = evaluation.load(ev["evaluation_id"])
    assert set(loaded["responses"]) == {"ramp", "brex"} and loaded["comparison"]["verdict"]
    assert {a["vendor"] for a in loaded["discovered"]} == {"ramp", "brex"}
    kinds = [e["event_type"] for e in events.read(ev["evaluation_id"])]
    assert kinds[:2] == ["evaluation_started", "registry_searched"] and "comparison_completed" in kinds


def test_requirements_payload_and_custom_question(tmp_eval_env):
    ev = evaluation.new_evaluation("sync expenses", custom_question="Do you support multi-entity sync?")
    r = ev["requirements"]
    assert r["schema"] == "judas.buyer_requirements/v1" and len(r["questions"]) == 7
    assert r["buyer"] == {"name": "Fictional Northstar Finance", "employees": 300, "synthetic": True}
    assert r["custom_question"] == "Do you support multi-entity sync?"
    assert evaluation.ID_RE.match(ev["evaluation_id"])


def test_context_id_is_evaluation_id(tmp_eval_env, monkeypatch):
    fake = _use(monkeypatch, FakeSellers())
    ev = evaluation.new_evaluation("x")
    asyncio.run(evaluation.contact(ev, "brex"))
    assert fake.calls[-1]["context_id"] == ev["evaluation_id"]
    assert fake.calls[-1]["agent_id"] == "brex-docs-adapter"


def test_seller_down_gives_partial_insufficient(tmp_eval_env, monkeypatch):
    _use(monkeypatch, FakeSellers(down={"brex"}))
    ev = evaluation.new_evaluation("x")
    asyncio.run(evaluation.evaluate(ev))
    assert ev["responses"]["brex"] is None and ev["comparison"]["verdict"] == "insufficient_evidence"
    assert ev["errors"]["brex"].startswith("ConnectionError")
    assert "exchange_failed" in [e["event_type"] for e in events.read(ev["evaluation_id"])]


def test_contact_timeout(tmp_eval_env, monkeypatch):
    _use(monkeypatch, FakeSellers(hang={"brex"}))
    monkeypatch.setattr(evaluation, "CONTACT_TIMEOUT_S", 0.2)
    ev = evaluation.new_evaluation("x")
    asyncio.run(evaluation.contact(ev, "brex"))
    assert ev["errors"]["brex"].startswith("TimeoutError")


def test_evaluate_contacts_concurrently(tmp_eval_env, monkeypatch):
    _use(monkeypatch, FakeSellers(hang={"brex", "ramp"}))
    monkeypatch.setattr(evaluation, "CONTACT_TIMEOUT_S", 0.5)
    import time
    t0 = time.perf_counter(); ev = evaluation.new_evaluation("x"); asyncio.run(evaluation.evaluate(ev))
    assert time.perf_counter() - t0 < 0.9          # two 0.5 s timeouts in parallel, not in series


def test_load_rejects_path_traversal(tmp_eval_env):
    with pytest.raises(KeyError):
        evaluation.load("../../etc/passwd")
    with pytest.raises(KeyError):
        evaluation.load("eval-20990101-abcdef")


class ForgingSeller(FakeSellers):
    """A seller that claims 'documented' with no evidence (what a dishonest vendor agent would do)."""

    async def __call__(self, agent_id, payload, context_id=None, agents=None):
        out = await super().__call__(agent_id, payload, context_id, agents)
        for a in out["response"]["answers"]:
            a["evidence"] = []
        return out


def test_buyer_rejects_unsubstantiated_vendor_response(tmp_eval_env, monkeypatch):
    _use(monkeypatch, ForgingSeller())
    ev = evaluation.new_evaluation("x")
    asyncio.run(evaluation.contact(ev, "brex"))
    assert ev["responses"]["brex"] is None
    assert ev["errors"]["brex"].startswith("InvalidVendorResponse")
    assert "exchange_failed" in [e["event_type"] for e in events.read(ev["evaluation_id"])]


class MalformedSeller(FakeSellers):
    async def __call__(self, agent_id, payload, context_id=None, agents=None):
        out = await super().__call__(agent_id, payload, context_id, agents)
        out["response"]["answers"] = [None, "garbage", {"finding": 7}]
        return out


def test_malformed_vendor_response_is_rejected_not_crash(tmp_eval_env, monkeypatch):
    _use(monkeypatch, MalformedSeller())
    ev = evaluation.new_evaluation("x")
    asyncio.run(evaluation.contact(ev, "ramp"))
    assert ev["responses"]["ramp"] is None and ev["errors"]["ramp"].startswith("InvalidVendorResponse")


class SlowSellers(FakeSellers):
    def __init__(self, delay):
        super().__init__(); self.delay = delay

    async def __call__(self, agent_id, payload, context_id=None, agents=None):
        await asyncio.sleep(self.delay)
        return await super().__call__(agent_id, payload, context_id, agents)
