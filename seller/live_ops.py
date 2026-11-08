"""Seller-side REAL operations for the live scenario, through each vendor's OFFICIAL hosted MCP server.

The adapter is ours (hackathon team); the MCP server and the product are the vendor's. Each run creates
a fresh project container inside one disposable JUDAS_TEST_ board/base (free-plan limits): a group on
monday.com, a table on Airtable. Only fictional data. Nothing outside JUDAS_TEST_ objects is touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from judas.contracts import envelope, error_payload
from vendors_live.mcp_live import ENDPOINTS, NotConfigured, timed_call, vendor_session

STATE = Path(__file__).resolve().parents[1] / "data" / "live" / "containers.json"
NEEDED = {"monday": {"get_board_info", "create_group", "create_item", "change_item_column_values"},
          "airtable": {"list_tables_for_base", "create_table", "create_records_for_table", "update_records_for_table"}}


def _state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1))


class Run:
    def __init__(self, vendor: str, client):
        self.vendor, self.c, self.steps = vendor, client, []

    async def call(self, test: str, tool: str, args: dict) -> dict:
        sc, err, ms, text = await timed_call(self.c, tool, args)
        self.steps.append({"test": test, "tool": tool, "ok": not err, "ms": ms,
                           "summary": (json.dumps(sc)[:300] if sc else text[:300])})
        if err:
            raise RuntimeError(f"{tool} failed: {text[:200]}")
        return sc or {}


def _find_id(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k):
            return d[k]
    for v in (d.values() if isinstance(d, dict) else []):
        if isinstance(v, dict):
            got = _find_id(v, *keys)
            if got:
                return got
    return None


async def _monday(run: Run, req: dict, tag: str) -> dict:
    st = _state(); m = st.setdefault("monday", {})
    board = m.get("board_id")
    if not board:
        sc = await run.call("A1", "create_board", {"boardName": f"JUDAS_TEST_Project_Tracking", "boardKind": "public",
                                                   "boardDescription": "Disposable Project Judas test board. Fictional data."})
        board = int(sc["board_id"]); m["board_id"] = board; _save_state(st)
    info = (await run.call("A1", "get_board_info", {"boardId": board})).get("board", {})
    cols = {c["title"]: c["id"] for c in info.get("columns", [])}
    for title in ("Status", "Priority"):
        if title not in cols:
            await run.call("A2", "create_column", {"boardId": board, "columnType": "status", "columnTitle": title})
    if "Status" not in cols or "Priority" not in cols:
        info = (await run.call("A1", "get_board_info", {"boardId": board})).get("board", {})
        cols = {c["title"]: c["id"] for c in info.get("columns", [])}
    gname = f"JUDAS run {tag}"
    sc = await run.call("A1", "create_group", {"boardId": str(board), "groupName": gname})   # this tool wants a string
    gid = _find_id(sc, "group_id", "groupId", "id")
    if not gid:
        info = (await run.call("A1", "get_board_info", {"boardId": board})).get("board", {})
        gid = next((g["id"] for g in info.get("groups", []) if g.get("title") == gname), None)
    ids = {}
    for t in req["tasks"]:
        sc = await run.call("A2", "create_item", {
            "boardId": board, "groupId": gid, "name": t["name"], "createLabelsIfMissing": True,
            "columnValues": json.dumps({cols["Status"]: {"label": t["status"]}, cols["Priority"]: {"label": t["priority"]}})})
        ids[t["name"]] = str(_find_id(sc, "item_id", "id"))
    up = req["update"]
    await run.call("A3", "change_item_column_values", {"boardId": board, "itemId": int(ids[up["name"]]),
                                                       "createLabelsIfMissing": True,
                                                       "columnValues": json.dumps({cols["Status"]: {"label": up["status"]}})})
    return {"container_kind": "monday group on a JUDAS_TEST_ board", "board_id": str(board), "group_id": gid,
            "group_name": gname, "item_ids": ids,
            "container_url": f"https://crafting-space.monday.com/boards/{board}"}


async def _airtable(run: Run, req: dict, tag: str) -> dict:
    st = _state(); a = st.setdefault("airtable", {})
    base = a.get("base_id")
    if not base:
        sc = await run.call("A1", "create_base", {"workspaceId": a["workspace_id"], "name": "JUDAS_TEST_Project_Tracking"})
        base = _find_id(sc, "id", "baseId"); a["base_id"] = base; _save_state(st)
    tname = f"JUDAS run {tag}"
    sel = lambda *n: {"choices": [{"name": x} for x in n]}  # noqa: E731
    sc = await run.call("A1", "create_table", {"baseId": base, "name": tname,
                                               "description": "Disposable Project Judas run. Fictional data.",
                                               "fields": [{"name": "Name", "type": "singleLineText"},
                                                          {"name": "Status", "type": "singleSelect", "options": sel("Open", "Investigating", "Resolved")},
                                                          {"name": "Priority", "type": "singleSelect", "options": sel("P1", "P2", "P3")}]})
    tables = (await run.call("A1", "list_tables_for_base", {"baseId": base})).get("tables") or []
    table = next(t for t in tables if t.get("name") == tname)
    tid, fid = table["id"], {f["name"]: f["id"] for f in table.get("fields", [])}
    sc = await run.call("A2", "create_records_for_table", {"baseId": base, "tableId": tid, "records": [
        {"fields": {fid["Name"]: t["name"], fid["Status"]: t["status"], fid["Priority"]: t["priority"]}} for t in req["tasks"]]})
    ids = {}
    for r in sc.get("records") or []:
        f = r.get("fields") or r.get("cellValuesByFieldId") or {}
        ids[f.get(fid["Name"]) or f.get("Name")] = r.get("id")
    up = req["update"]
    await run.call("A3", "update_records_for_table", {"baseId": base, "tableId": tid,
                                                      "records": [{"id": ids[up["name"]], "fields": {fid["Status"]: up["status"]}}]})
    return {"container_kind": "Airtable table in a JUDAS_TEST_ base", "base_id": base, "table_id": tid,
            "table_name": tname, "item_ids": ids, "container_url": f"https://airtable.com/{base}/{tid}"}


async def run_operations(req: dict, vendor: str, display: str) -> dict:
    eid = req.get("evaluation_id", "")
    tag = datetime.now(timezone.utc).strftime("%H%M%S")
    try:
        async with vendor_session(vendor, timeout=60) as c:
            tools = sorted(t.name for t in (await c.list_tools()).tools)
            run = Run(vendor, c)
            created = await (_monday(run, req, tag) if vendor == "monday" else _airtable(run, req, tag))
    except NotConfigured as e:
        return error_payload(eid, f"blocked: {e}")
    except BaseException as e:
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        return error_payload(eid, f"{display} operation failed: {type(e).__name__}: {str(e)[:240]}")
    return envelope("judas.operation_result/v1", eid, vendor,
                    interface={"kind": "official_vendor_mcp", "url": ENDPOINTS[vendor], "vendor_operated": True,
                               "adapter": f"Hackathon-owned A2A adapter, NOT operated by {display}"},
                    tools_available=tools, needed_tools=sorted(NEEDED[vendor]), steps=run.steps, created=created,
                    limitations=[f"The adapter is ours; the MCP server and the product are {display}'s.",
                                 "Fictional data in a disposable JUDAS_TEST_ workspace on a free plan."])
