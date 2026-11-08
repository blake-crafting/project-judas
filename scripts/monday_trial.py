"""Controlled real-vendor test on monday.com (official hosted MCP), with independent readback.
Writes only to the disposable JUDAS_TEST_ board passed as argv[1]. Evidence -> qa/vendor-probes/."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vendors_live.config import credential  # noqa: E402
from vendors_live.mcp_live import vendor_session, timed_call  # noqa: E402

BOARD = int(sys.argv[1])
TASKS = [("Configure CI pipeline", "To do", "High"), ("Review authentication flow", "In progress", "High"),
         ("Document release checklist", "To do", "Medium")]
UPDATE = ("Configure CI pipeline", "In progress")
steps: list[dict] = []


def log(step, tool, args, sc, err, ms, text):
    steps.append({"step": step, "interface": "monday official hosted MCP (vendor-operated)", "tool": tool,
                  "args": args, "error": err, "ms": ms, "result": sc if sc else text[:1500]})
    print(f"{step:<34} {tool:<26} err={err} {ms}ms")


async def graphql(query: str) -> dict:
    """Independent path: monday's public GraphQL API directly (not the MCP server)."""
    async with httpx2.AsyncClient(timeout=30) as h:
        r = await h.post("https://api.monday.com/v2", json={"query": query},
                         headers={"Authorization": credential("monday"), "API-Version": "2025-10"})
        return {"status": r.status_code, "body": r.json()}


async def main():
    async with vendor_session("monday") as c:
        cols = {}
        for title, labels in (("Status", None), ("Priority", None)):
            args = {"boardId": BOARD, "columnType": "status", "columnTitle": title}
            sc, err, ms, text = await timed_call(c, "create_column", args)
            log(f"create column {title}", "create_column", args, sc, err, ms, text)
        sc, err, ms, text = await timed_call(c, "get_board_info", {"boardId": BOARD})
        for col in (sc or {}).get("board", {}).get("columns", []):
            cols[col["title"]] = col["id"]
        print("column ids:", cols)
        ids = {}
        for name, status, prio in TASKS:
            args = {"boardId": BOARD, "name": name, "createLabelsIfMissing": True,
                    "columnValues": json.dumps({cols["Status"]: {"label": status}, cols["Priority"]: {"label": prio}})}
            sc, err, ms, text = await timed_call(c, "create_item", args)
            log(f"create item '{name}'", "create_item", args, sc, err, ms, text)
            iid = (sc or {}).get("item_id") or (sc or {}).get("id") or ((sc or {}).get("item") or {}).get("id")
            ids[name] = int(iid) if iid else None
        print("item ids:", ids)
        name, new = UPDATE
        args = {"boardId": BOARD, "itemId": ids[name], "createLabelsIfMissing": True,
                "columnValues": json.dumps({cols["Status"]: {"label": new}})}
        sc, err, ms, text = await timed_call(c, "change_item_column_values", args)
        log(f"update '{name}' -> {new}", "change_item_column_values", args, sc, err, ms, text)
        args = {"boardId": BOARD, "includeColumns": True, "limit": 25}
        sc, err, ms, text = await timed_call(c, "get_board_items_page", args)
        log("readback via MCP", "get_board_items_page", args, sc, err, ms, text)
        mcp_read = sc or text

    expected = {n: {"Status": UPDATE[1] if n == UPDATE[0] else s, "Priority": p} for n, s, p in TASKS}
    gq = await graphql(f'{{ boards(ids: [{BOARD}]) {{ name items_page(limit: 25) {{ items {{ id name '
                       f'column_values {{ id text column {{ title }} }} }} }} }} }}')
    items = gq["body"].get("data", {}).get("boards", [{}])[0].get("items_page", {}).get("items", [])
    actual = {it["name"]: {cv["column"]["title"]: cv["text"] for cv in it["column_values"]} for it in items}
    checks = [{"item": n, "field": f, "expected": v, "actual": actual.get(n, {}).get(f),
               "pass": actual.get(n, {}).get(f) == v} for n, e in expected.items() for f, v in e.items()]
    for ch in checks:
        print(f"  {'PASS' if ch['pass'] else 'FAIL'}  {ch['item']:<28} {ch['field']:<9} expected={ch['expected']!r} actual={ch['actual']!r}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "qa" / "vendor-probes" / f"monday-trial-{stamp}.json"
    out.write_text(json.dumps({
        "vendor": "monday.com", "account_tier": "free", "board_id": BOARD,
        "board_url": f"https://crafting-space.monday.com/boards/{BOARD}", "item_ids": ids,
        "steps": steps, "mcp_readback": mcp_read,
        "independent_readback": {"interface": "monday GraphQL API v2 (api.monday.com/v2), not the MCP server",
                                 "http_status": gq["status"], "items": actual},
        "checks": checks, "all_passed": all(ch["pass"] for ch in checks)}, indent=1))
    print("all checks passed:", all(ch["pass"] for ch in checks), "->", out.relative_to(ROOT))


asyncio.run(main())
