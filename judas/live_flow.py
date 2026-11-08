"""Buyer side of the live scenario: discover, request real operations over A2A, verify independently,
contact the vendor's own A2A agent where one exists, and compute evidence and a verdict."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx2

from buyer.client import load_registry, send_to_seller
from judas import live
from judas.contracts import envelope, validate
from vendors_live.config import credential, status
from web import events

ROOT = Path(__file__).resolve().parents[1]
LIVE_DIR = Path(os.environ.get("JUDAS_LIVE_DIR", ROOT / "data" / "live_evaluations"))
TIMEOUT_S = 120


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_live_evaluation() -> dict:
    eid = f"eval-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3)}"
    return {"evaluation_id": eid, "kind": "live", "created_at": _now(), "scenario": live.SCENARIO["id"],
            "discovered": [], "results": {}, "errors": {}, "readback": {}, "native_agent": {}, "evidence": {},
            "verdict": None}


def discover(ev: dict) -> list[dict]:
    cfg = status()
    agents = [a for a in load_registry() if "live_vendor_seller" in a.get("roles", [])]
    ev["discovered"] = [{**a, "configured": cfg.get(a["vendor"], False),
                         "official_mcp": live.VENDORS[a["vendor"]]["mcp"],
                         "native_a2a": live.VENDORS[a["vendor"]]["native_a2a"]} for a in agents]
    return ev["discovered"]


async def request_operations(ev: dict, vendor: str) -> None:
    eid, meta = ev["evaluation_id"], live.VENDORS[vendor]
    payload = envelope("judas.operation_request/v1", eid, scenario=live.SCENARIO["id"],
                       tasks=live.SCENARIO["tasks"], update=live.SCENARIO["update"],
                       ask="Demonstrate project tracking in our test workspace and report the real object ids.")
    await events.record(eid, "live_operation_requested", {"vendor": vendor, "protocol": "A2A (JSON-RPC, a2a-sdk)",
                                                          "tasks": len(payload["tasks"])}, agent_id=meta["agent_id"])
    try:
        out = await asyncio.wait_for(send_to_seller(meta["agent_id"], payload, context_id=eid), TIMEOUT_S)
        res = out["response"]
        if not isinstance(res, dict) or res.get("schema") != "judas.operation_result/v1":
            raise RuntimeError(str((res or {}).get("message") or "unexpected reply")[:300])
        problems = validate(res)
        if problems or res.get("vendor_id") != vendor:
            raise RuntimeError("rejected by the buyer: " + ("; ".join(problems) or "wrong vendor"))
    except Exception as e:
        ev["results"][vendor], ev["errors"][vendor] = None, f"{type(e).__name__}: {str(e)[:300]}"
        await events.record(eid, "live_operation_failed", {"vendor": vendor, "error": ev["errors"][vendor]},
                            agent_id=meta["agent_id"])
        return
    ev["results"][vendor] = res
    ev["errors"].pop(vendor, None)
    await events.record(eid, "live_operation_result", {
        "vendor": vendor, "interface": res["interface"]["url"], "vendor_operated": res["interface"]["vendor_operated"],
        "mcp_calls": len(res["steps"]), "tools_available": len(res["tools_available"]),
        "object_ids": res["created"].get("item_ids")}, agent_id=meta["agent_id"])


async def _read_monday(created: dict) -> tuple[dict, bool, int]:
    ids = ", ".join(str(i) for i in created["item_ids"].values())
    q = f"{{ items(ids: [{ids}]) {{ id name group {{ id }} board {{ id }} column_values {{ text column {{ title }} }} }} }}"
    async with httpx2.AsyncClient(timeout=30) as h:
        r = await h.post("https://api.monday.com/v2", json={"query": q},
                         headers={"Authorization": credential("monday") or "", "API-Version": "2025-10"})
    items = (r.json().get("data") or {}).get("items") or [] if r.status_code == 200 else []
    actual = {it["name"]: {cv["column"]["title"]: cv["text"] for cv in it["column_values"]} for it in items}
    inside = bool(items) and all(str(it["board"]["id"]) == created["board_id"] and it["group"]["id"] == created["group_id"]
                                 for it in items) and len(items) == len(created["item_ids"])
    return actual, inside, r.status_code


async def _read_airtable(created: dict) -> tuple[dict, bool, int]:
    from vendors_live.oauth import FileStorage, ensure_fresh
    await ensure_fresh("airtable")
    tok = await FileStorage("airtable").get_tokens()
    async with httpx2.AsyncClient(timeout=30) as h:
        r = await h.get(f"https://api.airtable.com/v0/{created['base_id']}/{created['table_id']}",
                        headers={"Authorization": f"Bearer {tok.access_token if tok else ''}"})
    recs = r.json().get("records", []) if r.status_code == 200 else []
    actual = {x["fields"].get("Name"): {"Status": x["fields"].get("Status"), "Priority": x["fields"].get("Priority")}
              for x in recs}
    inside = bool(recs) and {x["id"] for x in recs} >= set(created["item_ids"].values())
    return actual, inside, r.status_code


async def verify(ev: dict, vendor: str) -> None:
    """Independent readback through the vendor's separate public API (not the MCP server, not the seller)."""
    eid, res = ev["evaluation_id"], ev["results"].get(vendor)
    if not res:
        ev["readback"][vendor] = {"checks": [], "note": "nothing to verify"}
        return
    try:
        actual, inside, code = await (_read_monday if vendor == "monday" else _read_airtable)(res["created"])
        checks = live.check_readback(actual, inside)
    except Exception as e:
        actual, code, checks = {}, None, []
        ev["readback"][vendor] = {"checks": [], "error": f"{type(e).__name__}: {str(e)[:200]}"}
    else:
        ev["readback"][vendor] = {"interface": live.VENDORS[vendor]["readback"], "http_status": code,
                                  "actual": actual, "checks": checks}
    passed = sum(c["pass"] for c in checks)
    await events.record(eid, "live_verification", {"vendor": vendor, "interface": live.VENDORS[vendor]["readback"],
                                                   "checks_passed": passed, "checks_total": len(checks)})


async def contact_native_agent(ev: dict, vendor: str) -> None:
    """Talk to the vendor's OWN A2A agent, where one exists (monday.com Platform Agent)."""
    url = live.VENDORS[vendor]["native_a2a"]
    eid = ev["evaluation_id"]
    if not url:
        ev["native_agent"][vendor] = {"exists": False, "level": "none",
                                      "detail": "No vendor-operated A2A agent found (official docs, GitHub, agent-card URLs)."}
        return
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": {
        "messageId": str(uuid.uuid4()), "role": "ROLE_USER",
        "parts": [{"text": "How many items are on the board named JUDAS_TEST_Project_Tracking? Read-only; change nothing."}]}}}
    try:
        async with httpx2.AsyncClient(timeout=90) as h:
            r = await h.post(url, json=body, headers={"Authorization": f"Bearer {credential(vendor) or ''}",
                                                      "A2A-Version": "1.0", "Content-Type": "application/json"})
        msg = ((r.json().get("result") or {}).get("message") or {}) if r.status_code == 200 else {}
        text = " ".join(p.get("text", "") for p in msg.get("parts") or [])[:400]
        blocked = r.status_code != 200 or "credit" in text.lower() or "not enabled" in text.lower()
        ev["native_agent"][vendor] = {"exists": True, "endpoint": url, "http_status": r.status_code,
                                      "role": msg.get("role"), "reply": text or r.text[:200],
                                      "level": "blocked" if blocked else "answered",
                                      "credits_used": r.headers.get("X-Monday-Credits-Used")}
    except Exception as e:
        ev["native_agent"][vendor] = {"exists": True, "endpoint": url, "level": "unreachable",
                                      "reply": f"{type(e).__name__}: {str(e)[:200]}"}
    await events.record(eid, "vendor_agent_contacted", {"vendor": vendor, "protocol": "A2A 1.0 SendMessage",
                                                        "endpoint": url, "level": ev["native_agent"][vendor]["level"],
                                                        "reply": ev["native_agent"][vendor].get("reply", "")[:120]})


CRAFTING_TIMEOUT_S = 180


def _runner_command() -> list[str] | None:
    import shlex
    from vendors_live.config import _load_file
    raw = os.environ.get("CRAFTING_RUNNER") or _load_file().get("CRAFTING_RUNNER")
    return shlex.split(raw) if raw else None


async def _crafting_readback(vendor: str, resources: list[dict], ctx: dict) -> dict:
    """Judas's own check of what the Crafting runner says it created (vendor API, not the runner)."""
    ids = [str(r.get("id")) for r in resources if isinstance(r, dict) and r.get("id")]
    if not ids:
        return {"found": False, "note": "runner reported no resource ids"}
    rows = []
    async with httpx2.AsyncClient(timeout=30) as h:
        if vendor == "monday":
            q = f"{{ items(ids: [{', '.join(i for i in ids if i.isdigit())}]) {{ id name column_values {{ text column {{ title }} }} }} }}"
            r = await h.post("https://api.monday.com/v2", json={"query": q},
                             headers={"Authorization": credential("monday") or "", "API-Version": "2025-10"})
            for it in ((r.json().get("data") or {}).get("items") or []) if r.status_code == 200 else []:
                rows.append({"id": it["id"], "name": it["name"],
                             "fields": {cv["column"]["title"]: cv["text"] for cv in it["column_values"]}})
        else:
            from vendors_live.oauth import FileStorage, ensure_fresh
            await ensure_fresh("airtable")
            tok = await FileStorage("airtable").get_tokens()
            hdr = {"Authorization": f"Bearer {tok.access_token if tok else ''}"}
            base = ctx["airtable"]["base_id"]
            tables = list(dict.fromkeys(r.get("table_id") for r in resources if r.get("table_id")))
            if not tables:
                meta = await h.get(f"https://api.airtable.com/v0/meta/bases/{base}/tables", headers=hdr)
                tables = [t["id"] for t in meta.json().get("tables", [])] if meta.status_code == 200 else []
            for rid in ids:
                for t in tables:
                    r = await h.get(f"https://api.airtable.com/v0/{base}/{t}/{rid}", headers=hdr)
                    if r.status_code == 200:
                        f = r.json().get("fields", {})
                        rows.append({"id": rid, "name": f.get("Name") or f.get("Title"), "fields": f})
                        break
    # Each resource is checked against its own expected status (only the status-sync incident should be Resolved).
    expect = {str(r.get("id")): r.get("expected_status") for r in resources if isinstance(r, dict)}
    role = {str(r.get("id")): r.get("role") for r in resources if isinstance(r, dict)}
    checks = []
    for x in rows:
        exp = expect.get(str(x["id"]))
        got = str((x["fields"] or {}).get("Status", ""))
        if exp:
            checks.append({"id": x["id"], "role": role.get(str(x["id"])), "expected": exp, "actual": got,
                           "pass": got.lower() == str(exp).lower()})
    resolved = [c for c in checks if str(c["expected"]).lower() == "resolved"]
    status_ok = bool(checks) and all(c["pass"] for c in checks) and bool(resolved) if checks else any(
        str((x["fields"] or {}).get("Status", "")).lower() == "resolved" for x in rows)
    return {"found": len(rows) == len(ids), "read": len(rows), "reported": len(ids), "rows": rows[:5],
            "checks": checks, "status_ok": status_ok, "interface": live.VENDORS[vendor]["readback"]}


async def crafting_stage(ev: dict, vendor: str) -> None:
    """Stage 3: buyer-environment integration test in a Crafting sandbox (run by the Crafting runner)."""
    eid = ev["evaluation_id"]
    ev.setdefault("crafting", {})
    cmd = _runner_command()
    if not cmd:
        ev["crafting"][vendor] = {"status": "not_run", "reason": "Crafting runner not configured on this machine."}
        return
    from seller.live_ops import _state
    ctx = _state()
    req = {"schema": "judas.crafting_test_request/v1", "evaluation_id": eid, "vendor": vendor,
           "scenario": live.SCENARIO["id"], "context": ctx, "tests": [t["runner_id"] for t in live.CRAFTING_TESTS]}
    await events.record(eid, "crafting_test_started", {"vendor": vendor, "runner": os.path.basename(cmd[0])})
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, "--vendor", vendor, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(req).encode()), CRAFTING_TIMEOUT_S)
        res = json.loads(out.decode() or "{}")
        if res.get("schema") != "judas.crafting_test_result/v1" or res.get("vendor") != vendor:
            raise ValueError(f"unexpected runner output (exit {proc.returncode}): {out.decode()[:200]} {err.decode()[-200:]}")
    except Exception as e:
        ev["crafting"][vendor] = {"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}
        await events.record(eid, "crafting_test_result", {"vendor": vendor, "status": "error",
                                                          "error": ev["crafting"][vendor]["error"][:200]})
        return
    rb = await _crafting_readback(vendor, res.get("resources") or [], ctx)
    ev["crafting"][vendor] = {"status": "ran", "result": res, "judas_readback": rb}
    await events.record(eid, "crafting_test_result", {
        "vendor": vendor, "sandbox": (res.get("sandbox") or {}).get("name"),
        "tests": {t.get("id"): t.get("status") for t in res.get("tests") or []},
        "resources": [r.get("id") for r in res.get("resources") or []][:5],
        "judas_readback": f"{rb.get('read', 0)}/{rb.get('reported', 0)} found"})


def finalize(ev: dict) -> None:
    from seller.live_ops import NEEDED
    for v in live.VENDORS:
        ev["evidence"][v] = live.evidence(ev["results"].get(v), ev["errors"].get(v),
                                          (ev["readback"].get(v) or {}).get("checks", []), NEEDED[v])
    code, reason = live.verdict(ev["evidence"])
    ev["crafting_evidence"] = {v: live.crafting_evidence((ev.get("crafting") or {}).get(v)) for v in live.VENDORS}
    ccode, creason = live.compat_verdict(ev["crafting_evidence"])
    ev["verdict"] = {"code": code, "reason": reason, "compat_code": ccode, "compat_reason": creason,
                     "untested": live.UNTESTED, "caveat": live.CAVEAT}


async def run(ev: dict) -> dict:
    eid = ev["evaluation_id"]
    await events.record(eid, "evaluation_started", {"scenario": live.SCENARIO["id"], "kind": "live"})
    found = discover(ev)
    await events.record(eid, "registry_searched", {"registry": "local curated list", "role": "live_vendor_seller",
                                                   "results": [a["id"] for a in found]})
    await asyncio.gather(*(request_operations(ev, v) for v in live.VENDORS))
    await asyncio.gather(*(verify(ev, v) for v in live.VENDORS))
    await asyncio.gather(*(crafting_stage(ev, v) for v in live.VENDORS))
    finalize(ev)
    await events.record(eid, "comparison_completed", {"verdict": ev["verdict"]["code"]})
    return ev


def save(ev: dict) -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    p = LIVE_DIR / f"{ev['evaluation_id']}.json"
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(ev, indent=1)); os.replace(tmp, p)
