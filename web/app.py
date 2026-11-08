"""Buyer web app: serves the browser UI and a small JSON API that drives the
buyer agent. Every action calls real code (registry search, real A2A calls)
and writes events to the append-only log.

Run:  .venv/bin/python -m web.app        -> http://127.0.0.1:8700
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import Counter
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from buyer.client import ask_seller, load_registry, new_requirement, search_registry
from web import events

HOST = os.environ.get("WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_PORT", "8700"))
STATIC = Path(__file__).with_name("static")

SCENARIOS = [
    {
        "id": "software-procurement-api",
        "label": "Software procurement API",
        "buyer": "Fictional Northstar Engineering",
        "question": "Does Ramp expose API operations for expense and vendor records?",
        "requirements": ["vendor records", "expenses", "API documentation"],
    },
    {
        "id": "ap-automation",
        "label": "AP automation + webhooks",
        "buyer": "Fictional Northstar Engineering",
        "question": "We need an API to create bills and get webhook notifications. Do you also cover payroll?",
        "requirements": ["bills", "webhooks", "payroll"],
    },
]


class BadRequest(Exception):
    pass


def _err(status: int, message: str, hint: str | None = None) -> JSONResponse:
    return JSONResponse({"error": message, "hint": hint}, status_code=status)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise BadRequest("Request body must be JSON.")
    if not isinstance(body, dict):
        raise BadRequest("Request body must be a JSON object.")
    return body


def _clean_requirement(body: dict) -> dict:
    question = str(body.get("question", "")).strip()[:1000]
    reqs = body.get("requirements", [])
    if isinstance(reqs, str):
        reqs = reqs.split(",")
    if not isinstance(reqs, list):
        raise BadRequest("requirements must be a list of short strings.")
    reqs = [str(r).strip()[:100] for r in reqs if str(r).strip()][:10]
    if not question and not reqs:
        raise BadRequest("Type a question or at least one requirement.")
    if body.get("consent_to_share") is False:
        raise BadRequest("The buyer has not consented to share this question with sellers.")
    req = new_requirement(question, reqs, buyer=str(body.get("buyer") or "Fictional Northstar Engineering")[:100])
    if body.get("request_id"):
        req["request_id"] = str(body["request_id"])[:64]
    return req


def _session(body: dict) -> str:
    sid = str(body.get("session_id") or "").strip()[:64]
    return sid or f"sess-{uuid.uuid4().hex[:10]}"


# ---------- routes ----------

async def index(_: Request):
    return FileResponse(STATIC / "index.html")


async def api_scenarios(_: Request):
    return JSONResponse({"scenarios": SCENARIOS})


async def api_registry(_: Request):
    return JSONResponse({"agents": load_registry()})


async def api_discover(request: Request):
    try:
        body = await _json(request)
        req = _clean_requirement(body)
    except BadRequest as e:
        return _err(400, str(e))
    sid = _session(body)
    query = " ".join([req["question"], *req["requirements"]])
    await events.record(sid, "buyer_requirement_submitted", req)
    results = search_registry(query)
    await events.record(sid, "registry_searched", {
        "query": query, "registry": "local curated list (3 entries)",
        "result_count": len(results), "results": [r["id"] for r in results]})
    for r in results:
        await events.record(sid, "agent_discovered", {"name": r["name"], "score": r["score"],
                                                      "matched": r["matched"], "is_demo": r["is_demo"]},
                            agent_id=r["id"])
    return JSONResponse({"session_id": sid, "requirement": req, "results": results})


async def api_ask(request: Request):
    try:
        body = await _json(request)
        agent_id = str(body.get("agent_id", ""))[:64]
        req = _clean_requirement(body.get("requirement") or {})
    except BadRequest as e:
        return _err(400, str(e))
    sid = _session(body)
    registry = {a["id"]: a for a in load_registry()}
    if agent_id not in registry:
        return _err(400, f"Unknown agent {agent_id!r}. Only registry agents can be contacted.")

    try:
        result = await ask_seller(agent_id, req)
    except PermissionError as e:
        await events.record(sid, "exchange_failed", {"error": str(e)}, agent_id=agent_id)
        return _err(403, str(e))
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:300]}"
        await events.record(sid, "exchange_failed", {"error": msg}, agent_id=agent_id)
        return _err(502, f"Could not reach {registry[agent_id]['name']}.",
                    hint="The seller servers may not be running. Start them with ./dev.sh and try again. "
                         f"Details: {msg}")

    resp, trace = result["response"], {s["step"]: s for s in result["trace"]}
    card, sent, got = trace["agent_card_fetched"], trace["question_sent"], trace["seller_answer_received"]
    await events.record(sid, "seller_connected", {
        "agent_card_url": card["url"], "card_name": card["card_name"], "skills": card["skills"],
        "advertised_endpoints": trace["agent_card_validated"]["advertised_endpoints"],
        "protocol": "A2A (JSON-RPC, a2a-sdk)"}, agent_id=agent_id, timestamp=card["t"])
    await events.record(sid, "question_sent", {"method": sent["method"], "message_id": sent["message_id"],
                                               "payload": sent["payload"]}, agent_id=agent_id, timestamp=sent["t"])
    await events.record(sid, "seller_answer_received", {"task_id": got.get("task_id"), "task_state": got.get("state"),
                                                        "status": resp["status"], "ms": got.get("ms")},
                        agent_id=agent_id, timestamp=got["t"])
    sources = sorted({c["source_type"] for c in resp["claims"] if c.get("source_type")})
    if resp["evidence_mode"] == "live_official_docs":
        await events.record(sid, "evidence_retrieved", {
            "evidence_mode": resp["evidence_mode"], "sources": sources,
            "mcp_tool_calls": resp.get("tool_calls", [])}, agent_id=agent_id)
    else:
        await events.record(sid, "evidence_unavailable" if resp["evidence_mode"] == "local_fixture"
                            else "evidence_retrieved",
                            {"evidence_mode": resp["evidence_mode"], "sources": sources,
                             "note": resp["limitations"][0]}, agent_id=agent_id)
    counts = Counter(c["finding"] for c in resp["claims"])
    await events.record(sid, "evaluation_completed", {
        "request_id": resp["request_id"], "status": resp["status"], "findings": dict(counts),
        "unanswered": resp["unanswered"]}, agent_id=agent_id)

    return JSONResponse({"session_id": sid, "agent": result["agent"], "response": resp,
                         "trace": result["trace"], "events": events.read(sid)})


async def api_events(request: Request):
    sid = request.query_params.get("session_id")
    return JSONResponse({"events": events.read(sid[:64] if sid else None)})


async def api_reset(_: Request):
    await events.reset()
    return JSONResponse({"ok": True})


# ---------- Project Judas ----------

from judas import evaluation as judas_eval  # noqa: E402
from judas.contracts import MAX_CUSTOM_QUESTION  # noqa: E402
from judas.questions import QUESTIONS  # noqa: E402

MAX_REQUEST = 2000
_eval_locks: dict[str, asyncio.Lock] = {}


def _lock_for(eid: str) -> asyncio.Lock:
    return _eval_locks.setdefault(eid, asyncio.Lock())


async def judas_live_page(_: Request):
    return FileResponse(STATIC / "judas_live.html")


async def api_live_scenario(_: Request):
    from judas import live, live_flow
    from vendors_live.config import status
    cfg = status()
    return JSONResponse({"scenario": live.SCENARIO, "tests": live.TESTS, "untested": live.UNTESTED,
                         "crafting_tests": live.CRAFTING_TESTS,
                         "vendors": [{**meta, "id": v, "configured": cfg.get(v, False)} for v, meta in live.VENDORS.items()],
                         "agents": live_flow.discover(live_flow.new_live_evaluation())})


_live_task: asyncio.Task | None = None


async def _live_once() -> dict:
    from judas import live_flow
    ev = live_flow.new_live_evaluation()
    await live_flow.run(ev)
    live_flow.save(ev)
    return ev


async def api_live_run(_: Request):
    """Runs real operations in both vendors' test workspaces. One run at a time: a request that arrives
    while a run is in progress waits for that run and gets its result (joined=True)."""
    global _live_task
    joined = _live_task is not None and not _live_task.done()
    if not joined:
        _live_task = asyncio.create_task(_live_once())
    ev = await asyncio.shield(_live_task)
    return JSONResponse({**ev, "joined": joined, "events": events.read(ev["evaluation_id"])})


async def api_live_latest(_: Request):
    """The latest completed live run (for viewing; it is labeled as such on the page)."""
    from judas import live_flow
    files = sorted(live_flow.LIVE_DIR.glob("eval-*.json"), key=lambda f: f.stat().st_mtime) if live_flow.LIVE_DIR.exists() else []
    for f in reversed(files):
        import json as _json
        ev = _json.loads(f.read_text())
        if ev.get("scenario") == "incident_tracking_v1" and ev.get("verdict"):
            return JSONResponse({**ev, "events": events.read(ev["evaluation_id"])})
    return _err(404, "No completed live run yet.")


async def judas_page(_: Request):
    return FileResponse(STATIC / "judas.html")


BUYER_PROFILES = {
    "default": {"label": "Balanced (default)", "priorities": judas_eval.clean_priorities(None)},
    "integrity": {"label": "Data integrity first",
                  "priorities": {"Q1": "nice", "Q2": "must", "Q3": "must", "Q4": "must", "Q5": "must",
                                 "Q6": "nice", "Q7": "nice"}},
    "realtime": {"label": "Real-time first",
                 "priorities": {"Q1": "must", "Q2": "nice", "Q3": "nice", "Q4": "must", "Q5": "nice",
                                "Q6": "must", "Q7": "nice"}},
}


async def api_judas_scenario(_: Request):
    return JSONResponse({"label": "Expense sync: Ramp vs Brex", "request": judas_eval.DEFAULT_REQUEST,
                         "requirements": [q.requirement for q in QUESTIONS],
                         "questions": [{"id": q.id, "requirement": q.requirement, "ask": q.ask} for q in QUESTIONS],
                         "profiles": BUYER_PROFILES})


def _load_eval(request: Request) -> dict:
    return judas_eval.load(request.path_params["eid"])


async def api_judas_discover(request: Request):
    try:
        body = await _json(request)
    except BadRequest as e:
        return _err(400, str(e))
    text = str(body.get("request") or "").strip()
    if not text:
        return _err(400, "Enter a buyer request (or load the sample scenario).")
    if len(text) > MAX_REQUEST:
        return _err(400, f"The buyer request is too long (max {MAX_REQUEST} characters).")
    custom = body.get("custom_question")
    custom = str(custom).strip() if custom else None
    if custom and len(custom) > MAX_CUSTOM_QUESTION:
        return _err(400, f"The custom question is too long (max {MAX_CUSTOM_QUESTION} characters).")
    prio = body.get("priorities")
    if prio is not None and not isinstance(prio, dict):
        return _err(400, "priorities must be an object of question id -> must | nice | skip.")
    ev = judas_eval.new_evaluation(text, custom_question=custom or None, priorities=prio)
    if not ev["requirements"]["questions"]:
        return _err(400, "Keep at least one requirement (every requirement is set to skip).")
    agents = await judas_eval.discover(ev)
    judas_eval.save(ev)
    return JSONResponse({"evaluation_id": ev["evaluation_id"], "requirements": ev["requirements"], "agents": agents})


async def api_judas_contact(request: Request):
    try:
        body = await _json(request)
    except BadRequest as e:
        return _err(400, str(e))
    vendor = str(body.get("vendor", ""))
    if vendor not in judas_eval.VENDOR_AGENTS:
        return _err(400, f"Unknown vendor {vendor!r}. Choose ramp or brex.")
    async with _lock_for(request.path_params["eid"]):
        try:
            ev = _load_eval(request)
        except KeyError:
            return _err(404, "Unknown evaluation.", hint="Click Discover agents to start a new evaluation.")
        resp = await judas_eval.contact(ev, vendor)
        judas_eval.save(ev)
    hint = None
    if ev["errors"].get(vendor):
        hint = "The seller agents may not be running. Start them with ./dev.sh and try again."
    return JSONResponse({"vendor": vendor, "response": resp, "trace": ev["traces"].get(vendor, []),
                         "error": ev["errors"].get(vendor), "hint": hint,
                         "events": events.read(ev["evaluation_id"])})


async def api_judas_evaluate(request: Request):
    try:
        body = await _json(request)
    except BadRequest:
        body = {}
    prio = body.get("priorities") if isinstance(body, dict) else None
    async with _lock_for(request.path_params["eid"]):
        try:
            ev = _load_eval(request)
        except KeyError:
            return _err(404, "Unknown evaluation.", hint="Click Discover agents to start a new evaluation.")
        if isinstance(prio, dict):
            judas_eval.set_priorities(ev, prio)
            if not ev["requirements"]["questions"]:
                return _err(400, "Keep at least one requirement (every requirement is set to skip).")
        await judas_eval.evaluate(ev)
        judas_eval.save(ev)
    return JSONResponse(ev)


async def api_judas_demo(request: Request):
    try:
        body = await _json(request)
    except BadRequest as e:
        return _err(400, str(e))
    vendor = str(body.get("vendor", ""))
    if vendor not in judas_eval.VENDOR_AGENTS:
        return _err(400, f"Unknown vendor {vendor!r}. Choose ramp or brex.")
    async with _lock_for(request.path_params["eid"]):
        try:
            ev = _load_eval(request)
        except KeyError:
            return _err(404, "Unknown evaluation.", hint="Click Discover agents to start a new evaluation.")
        res = await judas_eval.request_demo(ev, vendor)
        judas_eval.save(ev)
    ok = res.get("schema") == "judas.demo_result/v1"
    return JSONResponse({"vendor": vendor, "demo": res if ok else None, "error": None if ok else res.get("message"),
                         "hint": None if ok else "The demo sandbox may not be running. Start it with ./dev.sh.",
                         "events": events.read(ev["evaluation_id"])})


async def api_judas_suggest(request: Request):
    from judas.priorities import suggest
    try:
        body = await _json(request)
    except BadRequest as e:
        return _err(400, str(e))
    text = str(body.get("request") or "").strip()
    if not text:
        return _err(400, "Type the buyer request first, then ask for suggestions.")
    return JSONResponse(suggest(text[:MAX_REQUEST]))


async def api_judas_challenge(request: Request):
    try:
        body = await _json(request)
    except BadRequest as e:
        return _err(400, str(e))
    vendor, qid = str(body.get("vendor", "")), str(body.get("question_id", ""))
    if vendor not in judas_eval.VENDOR_AGENTS:
        return _err(400, f"Unknown vendor {vendor!r}. Choose ramp or brex.")
    if qid not in {q.id for q in QUESTIONS}:
        return _err(400, f"Unknown requirement {qid!r}.")
    async with _lock_for(request.path_params["eid"]):
        try:
            ev = _load_eval(request)
        except KeyError:
            return _err(404, "Unknown evaluation.", hint="Click Discover agents to start a new evaluation.")
        res = await judas_eval.challenge(ev, vendor, qid)
        judas_eval.save(ev)
    ok = res.get("schema") == "judas.follow_up_result/v1"
    return JSONResponse({"evaluation": ev, "error": None if ok else res.get("message")})


async def api_judas_get(request: Request):
    try:
        return JSONResponse(_load_eval(request))
    except KeyError:
        return _err(404, "Unknown evaluation.")


async def api_judas_events(request: Request):
    try:
        ev = _load_eval(request)
    except KeyError:
        return _err(404, "Unknown evaluation.")
    return JSONResponse({"events": events.read(ev["evaluation_id"])})


async def api_judas_reset(_: Request):
    return JSONResponse({"archived": judas_eval.archive_all()})


def build_app() -> Starlette:
    return Starlette(routes=[
        Route("/", index),
        Route("/api/scenarios", api_scenarios),
        Route("/api/registry", api_registry),
        Route("/api/discover", api_discover, methods=["POST"]),
        Route("/api/ask", api_ask, methods=["POST"]),
        Route("/api/events", api_events),
        Route("/api/reset", api_reset, methods=["POST"]),
        Route("/judas", judas_page),
        Route("/judas/live", judas_live_page),
        Route("/api/live/scenario", api_live_scenario),
        Route("/api/live/run", api_live_run, methods=["POST"]),
        Route("/api/live/latest", api_live_latest),
        Route("/api/judas/scenario", api_judas_scenario),
        Route("/api/judas/discover", api_judas_discover, methods=["POST"]),
        Route("/api/judas/suggest-priorities", api_judas_suggest, methods=["POST"]),
        Route("/api/judas/evaluations/{eid}", api_judas_get),
        Route("/api/judas/evaluations/{eid}/contact", api_judas_contact, methods=["POST"]),
        Route("/api/judas/evaluations/{eid}/evaluate", api_judas_evaluate, methods=["POST"]),
        Route("/api/judas/evaluations/{eid}/demo", api_judas_demo, methods=["POST"]),
        Route("/api/judas/evaluations/{eid}/challenge", api_judas_challenge, methods=["POST"]),
        Route("/api/judas/evaluations/{eid}/events", api_judas_events),
        Route("/api/judas/reset", api_judas_reset, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
