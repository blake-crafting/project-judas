"""Controlled real-vendor test on Airtable (official hosted MCP, OAuth), with independent readback
through Airtable's REST API. Creates one disposable JUDAS_TEST_ base. Evidence -> qa/vendor-probes/."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vendors_live.mcp_live import vendor_session, timed_call  # noqa: E402
from vendors_live.oauth import FileStorage  # noqa: E402

WORKSPACE = sys.argv[1]
TASKS = [("Configure CI pipeline", "To do", "High"), ("Review authentication flow", "In progress", "High"),
         ("Document release checklist", "To do", "Medium")]
UPDATE = ("Configure CI pipeline", "In progress")
STAMP = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
steps: list[dict] = []


def log(step, tool, args, sc, err, ms, text):
    steps.append({"step": step, "interface": "Airtable official hosted MCP (vendor-operated)", "tool": tool,
                  "args": args, "error": err, "ms": ms, "result": sc if sc else text[:1500]})
    print(f"{step:<34} {tool:<26} err={err} {ms}ms" + ("" if not err else f"  {text[:300]}"))


def choices(*names):
    return {"choices": [{"name": n} for n in names]}


async def main():
    async with vendor_session("airtable") as c:
        args = {"workspaceId": WORKSPACE, "name": f"JUDAS_TEST_Project_Tracking_{STAMP}",
                "tables": [{"name": "Tasks", "description": "Disposable Project Judas test table. Fictional data.",
                            "fields": [{"name": "Name", "type": "singleLineText"},
                                       {"name": "Status", "type": "singleSelect", "options": choices("To do", "In progress", "Done")},
                                       {"name": "Priority", "type": "singleSelect", "options": choices("High", "Medium", "Low")}]}]}
        sc, err, ms, text = await timed_call(c, "create_base", args)
        log("create base + Tasks table", "create_base", args, sc, err, ms, text)
        base = (sc or {}).get("id") or (sc or {}).get("baseId") or ((sc or {}).get("base") or {}).get("id")
        if not base:
            print("no base id in response:", json.dumps(sc)[:500] if sc else text[:500]); return
        sc, err, ms, text = await timed_call(c, "list_tables_for_base", {"baseId": base})
        log("read table schema", "list_tables_for_base", {"baseId": base}, sc, err, ms, text)
        tables = (sc or {}).get("tables") or []
        table = next(t for t in tables if t.get("name") == "Tasks")
        tid = table["id"]
        fid = {f["name"]: f["id"] for f in table.get("fields", [])}
        print("base", base, "table", tid, "fields", fid)
        args = {"baseId": base, "tableId": tid, "records": [
            {"fields": {fid["Name"]: n, fid["Status"]: s, fid["Priority"]: p}} for n, s, p in TASKS]}
        sc, err, ms, text = await timed_call(c, "create_records_for_table", args)
        log("create 3 records", "create_records_for_table", args, sc, err, ms, text)
        recs = (sc or {}).get("records") or []
        ids = {}
        for r in recs:
            f = r.get("fields") or r.get("cellValuesByFieldId") or {}
            nm = f.get(fid["Name"]) or f.get("Name")
            ids[nm] = r.get("id")
        print("record ids:", ids)
        name, new = UPDATE
        args = {"baseId": base, "tableId": tid, "records": [{"id": ids[name], "fields": {fid["Status"]: new}}]}
        sc, err, ms, text = await timed_call(c, "update_records_for_table", args)
        log(f"update '{name}' -> {new}", "update_records_for_table", args, sc, err, ms, text)
        sc, err, ms, text = await timed_call(c, "list_records_for_table", {"baseId": base, "tableId": tid})
        log("readback via MCP", "list_records_for_table", {"baseId": base, "tableId": tid}, sc, err, ms, text)
        mcp_read = sc or text

    tok = (await FileStorage("airtable").get_tokens()).access_token
    async with httpx2.AsyncClient(timeout=30) as h:                  # independent path: REST API, not MCP
        r = await h.get(f"https://api.airtable.com/v0/{base}/{tid}", headers={"Authorization": f"Bearer {tok}"})
    rest = r.json() if r.status_code == 200 else {"error": r.text[:300]}
    actual = {rec["fields"].get("Name"): {"Status": rec["fields"].get("Status"), "Priority": rec["fields"].get("Priority")}
              for rec in rest.get("records", [])}
    expected = {n: {"Status": UPDATE[1] if n == UPDATE[0] else s, "Priority": p} for n, s, p in TASKS}
    checks = [{"item": n, "field": f, "expected": v, "actual": actual.get(n, {}).get(f),
               "pass": actual.get(n, {}).get(f) == v} for n, e in expected.items() for f, v in e.items()]
    for ch in checks:
        print(f"  {'PASS' if ch['pass'] else 'FAIL'}  {ch['item']:<28} {ch['field']:<9} expected={ch['expected']!r} actual={ch['actual']!r}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "qa" / "vendor-probes" / f"airtable-trial-{stamp}.json"
    out.write_text(json.dumps({
        "vendor": "Airtable", "account_tier": "free", "auth": "OAuth 2.0 (authorization code + PKCE, dynamic client registration)",
        "base_id": base, "table_id": tid, "base_url": f"https://airtable.com/{base}/{tid}", "record_ids": ids,
        "steps": steps, "mcp_readback": mcp_read,
        "independent_readback": {"interface": "Airtable REST API (api.airtable.com/v0), not the MCP server",
                                 "http_status": r.status_code, "items": actual},
        "checks": checks, "all_passed": all(ch["pass"] for ch in checks)}, indent=1))
    print("all checks passed:", all(ch["pass"] for ch in checks), "->", out.relative_to(ROOT))
    print("open:", f"https://airtable.com/{base}/{tid}")


asyncio.run(main())
