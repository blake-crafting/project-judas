"""Judas evaluation lifecycle: discover vendor agents, contact them over A2A
(context_id = evaluation_id), compare, persist, and log every step."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from buyer.client import load_registry, search_registry, send_to_seller
from judas.compare import compare
from judas.contracts import (BUYER_REQUIREMENTS, DEMO_REQUEST, DEMO_RESULT, FOLLOW_UP, FOLLOW_UP_RESULT,
                             VENDOR_RESPONSE, envelope, error_payload, validate)
from judas.questions import QUESTIONS, questions_payload
from web import events

ROOT = Path(__file__).resolve().parents[1]
VENDOR_AGENTS = {"ramp": "ramp-docs-adapter", "brex": "brex-docs-adapter"}
EVALUATIONS_DIR = Path(os.environ.get("JUDAS_EVALUATIONS_DIR", ROOT / "data" / "evaluations"))
CONTACT_TIMEOUT_S = 20
ID_RE = re.compile(r"^eval-\d{8}-[0-9a-f]{6}$")
BUYER = {"name": "Fictional Northstar Finance", "employees": 300, "synthetic": True}
DEFAULT_REQUEST = ("We are a 300-person company evaluating Ramp and Brex. We need to synchronize corporate "
                   "expense data into an internal finance system. We want a reliable integration, preservation "
                   "of original transaction identifiers, correct currency and amount handling, and no duplicate "
                   "records when data is imported repeatedly. Evaluate both vendors and compare what their "
                   "documentation says about our requirements.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def clean_priorities(raw: dict | None) -> dict[str, str]:
    base = {q.id: ("must" if q.must_have else "nice") for q in QUESTIONS}
    for k, v in (raw or {}).items() if isinstance(raw, dict) else []:
        if k in base and v in ("must", "nice", "skip"):
            base[k] = v
    return base


def _requirements(eid: str, request_text: str, custom_question: str | None, priorities: dict) -> dict:
    """Only the requirements the buyer kept are asked; each carries its priority over A2A."""
    extra = {"custom_question": custom_question} if custom_question else {}
    qs = [{**q, "priority": priorities[q["id"]]} for q in questions_payload() if priorities[q["id"]] != "skip"]
    return envelope(BUYER_REQUIREMENTS, eid, buyer=dict(BUYER), request=request_text, questions=qs,
                    priorities=priorities, **extra)


def new_evaluation(request_text: str, custom_question: str | None = None, priorities: dict | None = None) -> dict:
    eid = f"eval-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3)}"
    prio = clean_priorities(priorities)
    return {"evaluation_id": eid, "created_at": _now(), "priorities": prio,
            "requirements": _requirements(eid, request_text, custom_question, prio), "discovered": [],
            "responses": {}, "traces": {}, "errors": {}, "demos": {}, "follow_ups": [], "comparison": None}


def set_priorities(evaluation: dict, priorities: dict | None) -> bool:
    """Apply new buyer priorities. If a requirement that was skipped is now wanted, the sellers
    must be asked again (returns True and clears their answers); otherwise only the verdict changes."""
    if priorities is None:
        return False
    old, new = evaluation.get("priorities") or clean_priorities(None), clean_priorities(priorities)
    evaluation["priorities"] = new
    req = evaluation["requirements"]
    evaluation["requirements"] = _requirements(evaluation["evaluation_id"], req["request"],
                                               req.get("custom_question"), new)
    reask = any(old.get(q) == "skip" and new[q] != "skip" for q in new)
    if reask:
        evaluation["responses"] = {}
    return reask


async def discover(evaluation: dict) -> list[dict]:
    eid = evaluation["evaluation_id"]
    await events.record(eid, "evaluation_started", evaluation["requirements"])
    query = evaluation["requirements"]["request"] + " ramp brex"
    scores = {h["id"]: h for h in search_registry(query)}
    vendors = [a for a in load_registry() if "vendor_seller" in a.get("roles", [])]
    found = []
    for a in vendors:
        hit = scores.get(a["id"], {})
        found.append({**a, "score": hit.get("score", 0), "matched": hit.get("matched", [])})
    found.sort(key=lambda a: -a["score"])
    evaluation["discovered"] = found
    await events.record(eid, "registry_searched", {"query": query[:300], "registry": "local curated list",
                                                   "result_count": len(found), "results": [a["id"] for a in found]})
    for a in found:
        await events.record(eid, "agent_discovered", {"name": a["name"], "vendor": a.get("vendor"),
                                                      "score": a["score"], "is_demo": a.get("is_demo")},
                            agent_id=a["id"])
    return found


async def contact(evaluation: dict, vendor: str) -> dict:
    eid = evaluation["evaluation_id"]
    agent_id = VENDOR_AGENTS[vendor]
    await events.record(eid, "vendor_contacted", {"vendor": vendor, "protocol": "A2A (JSON-RPC, a2a-sdk)",
                                                  "context_id": eid}, agent_id=agent_id)
    try:
        out = await asyncio.wait_for(send_to_seller(agent_id, evaluation["requirements"], context_id=eid),
                                     CONTACT_TIMEOUT_S)
    except Exception as e:  # network, timeout, protocol
        msg = f"{type(e).__name__}: {str(e)[:300]}"
        evaluation["responses"][vendor] = None
        evaluation["errors"][vendor] = msg
        await events.record(eid, "exchange_failed", {"vendor": vendor, "error": msg}, agent_id=agent_id)
        return error_payload(eid, msg)
    resp = out["response"]
    # The buyer is the trust boundary: a seller's answer counts only if it is a well-formed
    # vendor_response whose 'documented' claims carry real MCP evidence.
    try:
        problems = (validate(resp) if isinstance(resp, dict) and resp.get("schema") == VENDOR_RESPONSE
                    else [f"expected {VENDOR_RESPONSE}, got {getattr(resp, 'get', lambda k: None)('schema')!r}"])
    except Exception as e:  # validation itself must never take the buyer down
        problems = [f"unreadable response ({type(e).__name__})"]
    if problems:
        msg = "InvalidVendorResponse: " + "; ".join(problems)[:300]
        evaluation["responses"][vendor] = None
        evaluation["errors"][vendor] = msg
        await events.record(eid, "exchange_failed", {"vendor": vendor, "error": msg,
                                                     "note": "response rejected by the buyer; claims not counted"},
                            agent_id=agent_id)
        return error_payload(eid, msg)
    evaluation["responses"][vendor] = resp
    evaluation["traces"][vendor] = out.get("trace", [])
    evaluation["errors"].pop(vendor, None)
    findings: dict[str, int] = {}
    for a in resp.get("answers") or []:
        findings[a.get("finding")] = findings.get(a.get("finding"), 0) + 1
    await events.record(eid, "vendor_response_received",
                        {"vendor": vendor, "schema": resp.get("schema"), "findings": findings,
                         "mcp_tool_calls": len(resp.get("tool_calls") or [])}, agent_id=agent_id)
    return resp


async def request_demo(evaluation: dict, vendor: str) -> dict:
    """Ask a seller over A2A to DEMONSTRATE card-transaction sync (not just describe it)."""
    eid = evaluation["evaluation_id"]
    agent_id = VENDOR_AGENTS[vendor]
    payload = envelope(DEMO_REQUEST, eid, demo="card_transaction_sync",
                       ask=("Demonstrate incremental card-transaction sync: page two overlapping windows, "
                            "de-duplicate by id, normalize amounts to integer minor units."))
    await events.record(eid, "demo_requested", {"vendor": vendor, "protocol": "A2A (JSON-RPC, a2a-sdk)",
                                                "demo": payload["demo"]}, agent_id=agent_id)
    evaluation.setdefault("demos", {})
    try:
        out = await asyncio.wait_for(send_to_seller(agent_id, payload, context_id=eid), CONTACT_TIMEOUT_S)
        res = out["response"]
        problems = (validate(res) if isinstance(res, dict) and res.get("schema") == DEMO_RESULT
                    else [str((res or {}).get("message") or f"expected {DEMO_RESULT}")[:300]])
    except Exception as e:
        res, problems = None, [f"{type(e).__name__}: {str(e)[:300]}"]
    if problems:
        evaluation["demos"][vendor] = None
        await events.record(eid, "demo_failed", {"vendor": vendor, "error": "; ".join(problems)[:300]},
                            agent_id=agent_id)
        return error_payload(eid, "; ".join(problems)[:300])
    evaluation["demos"][vendor] = res
    await events.record(eid, "demo_result_received",
                        {"vendor": vendor, "evidence_level": res["evidence_level"],
                         "vendor_hosted": res["endpoint"].get("vendor_hosted"), "mcp_tool_calls": len(res["tool_calls"]),
                         "duplicates_removed": res.get("duplicates_removed")}, agent_id=agent_id)
    return res


async def challenge(evaluation: dict, vendor: str, question_id: str) -> dict:
    """The buyer challenges one unresolved requirement: name the missing parts, ask the seller over A2A
    to look again, keep the original answer, and recompute the comparison with the re-checked one."""
    eid = evaluation["evaluation_id"]
    agent_id = VENDOR_AGENTS[vendor]
    resp = evaluation["responses"].get(vendor) or {}
    answers = resp.get("answers") or []
    idx = next((i for i, a in enumerate(answers) if isinstance(a, dict) and a.get("question_id") == question_id), None)
    if idx is None:
        return error_payload(eid, f"No {vendor} answer for {question_id} to challenge yet.")
    original = answers[idx]
    missing = [f.get("name") for f in original.get("facets") or [] if f.get("core") and not f.get("found")]
    if not missing:
        return error_payload(eid, f"Nothing unresolved in {vendor}'s answer to {question_id}.")
    payload = envelope(FOLLOW_UP, eid, question_id=question_id, missing=missing,
                       ask=f"Your answer did not establish: {'; '.join(missing)}. What official evidence supports it?")
    await events.record(eid, "follow_up_sent", {"vendor": vendor, "question_id": question_id, "missing": missing,
                                                "protocol": "A2A (JSON-RPC, a2a-sdk)"}, agent_id=agent_id)
    try:
        out = await asyncio.wait_for(send_to_seller(agent_id, payload, context_id=eid), CONTACT_TIMEOUT_S)
        res = out["response"]
        problems = (validate(res) if isinstance(res, dict) and res.get("schema") == FOLLOW_UP_RESULT
                    else [str((res or {}).get("message") or f"expected {FOLLOW_UP_RESULT}")[:300]])
        if not problems and (res.get("question_id") != question_id or res.get("vendor_id") != vendor):
            problems = ["follow-up answer is for a different vendor or question"]
    except Exception as e:
        res, problems = None, [f"{type(e).__name__}: {str(e)[:300]}"]
    if problems:
        await events.record(eid, "follow_up_failed", {"vendor": vendor, "question_id": question_id,
                                                      "error": "; ".join(problems)[:300]}, agent_id=agent_id)
        return error_payload(eid, "; ".join(problems)[:300])
    evaluation.setdefault("follow_ups", []).append({
        "vendor": vendor, "question_id": question_id, "at": _now(), "missing": missing,
        "investigations": res.get("investigations") or [], "original_answer": original, "answer": res["answer"]})
    new_resp = {**resp, "answers": [res["answer"] if i == idx else a for i, a in enumerate(answers)]}
    evaluation["responses"][vendor] = new_resp
    inv = res.get("investigations") or []
    await events.record(eid, "follow_up_received",
                        {"vendor": vendor, "question_id": question_id, "finding": res["answer"].get("finding"),
                         "resolved": [i.get("facet") for i in inv if i.get("resolved")],
                         "still_missing": [i.get("facet") for i in inv if not i.get("resolved")],
                         "mcp_tool_calls": len(res.get("tool_calls") or [])}, agent_id=agent_id)
    await finalize(evaluation)
    return res


async def evaluate(evaluation: dict, with_demos: bool = True) -> dict:
    todo = [v for v in VENDOR_AGENTS if evaluation["responses"].get(v) is None]
    demos = [v for v in VENDOR_AGENTS if with_demos and not (evaluation.get("demos") or {}).get(v)]
    await asyncio.gather(*(contact(evaluation, v) for v in todo), *(request_demo(evaluation, v) for v in demos))
    return await finalize(evaluation)


async def finalize(evaluation: dict) -> dict:
    eid = evaluation["evaluation_id"]
    evaluation["comparison"] = c = compare(eid, {v: evaluation["responses"].get(v) for v in VENDOR_AGENTS},
                                           priorities=evaluation.get("priorities"), demos=evaluation.get("demos"),
                                           follow_ups=evaluation.get("follow_ups"))
    await events.record(eid, "comparison_completed", {"verdict": c["verdict"], "coverage": c["coverage"],
                                                      "must_haves": c["explanation"]["mattered"],
                                                      "unresolved": [u["question_id"] for u in c["explanation"]["unresolved"]],
                                                      "clarifications": len(c["clarifications"])})
    return evaluation


def _path(evaluation_id: str) -> Path:
    if not isinstance(evaluation_id, str) or not ID_RE.match(evaluation_id):
        raise KeyError(evaluation_id)
    return EVALUATIONS_DIR / f"{evaluation_id}.json"


def save(evaluation: dict) -> None:
    p = _path(evaluation["evaluation_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(evaluation, indent=1))
    os.replace(tmp, p)                      # readers never see a partial file


def load(evaluation_id: str) -> dict:
    p = _path(evaluation_id)
    if not p.exists():
        raise KeyError(evaluation_id)
    return json.loads(p.read_text())


def archive_all() -> str:
    if not EVALUATIONS_DIR.exists():
        return ""
    dest = ROOT / "data" / "archive" / f"evaluations-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(EVALUATIONS_DIR), str(dest))
    return str(dest)
