"""One-time setup of the vendor-side containers used by the Crafting incident evaluation.

Only touches the existing disposable JUDAS_TEST_ containers (ids in data/live/containers.json):
  - Airtable: adds table `JUDAS_CRAFTING_incidents` to the JUDAS_TEST_ base.
  - monday.com: adds text columns (Incident Key, Order ID, Test Run) and a group
    `JUDAS Crafting incidents` to the JUDAS_TEST_ board.
Idempotent. Writes ids (no secrets) to data/crafting/containers.json. Fictional data only.
Run: python -m crafting.setup_vendors
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from crafting.creds import CREDS_ROOT, STATE_DIR, airtable_access_token, monday_token

TABLE = "JUDAS_CRAFTING_incidents"
GROUP = "JUDAS Crafting incidents"


def _req(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")


def setup_airtable(base: str) -> dict:
    h = {"Authorization": f"Bearer {airtable_access_token()}"}
    tables = _req("GET", f"https://api.airtable.com/v0/meta/bases/{base}/tables", h).get("tables", [])
    t = next((x for x in tables if x["name"] == TABLE), None)
    if not t:
        sel = lambda *n: {"choices": [{"name": x} for x in n]}  # noqa: E731
        t = _req("POST", f"https://api.airtable.com/v0/meta/bases/{base}/tables", h, {
            "name": TABLE, "description": "Project Judas x Crafting incident-sync evaluation. Fictional data.",
            "fields": [{"name": "Name", "type": "singleLineText"},
                       {"name": "Incident Key", "type": "singleLineText"},
                       {"name": "Status", "type": "singleSelect", "options": sel("Open", "Investigating", "Resolved")},
                       {"name": "Severity", "type": "singleSelect", "options": sel("P1", "P2", "P3")},
                       {"name": "Order ID", "type": "singleLineText"},
                       {"name": "Test Run", "type": "singleLineText"}]})
    return {"base_id": base, "table_id": t["id"], "table_name": TABLE,
            "url": f"https://airtable.com/{base}/{t['id']}"}


def _monday(q: str, v: dict | None = None) -> dict:
    tok = monday_token()
    if not tok:
        raise RuntimeError("monday login not configured on the Judas host")
    r = _req("POST", "https://api.monday.com/v2", {"Authorization": tok, "API-Version": "2025-10"},
             {"query": q, "variables": v or {}})
    if r.get("errors") or r.get("error_message"):
        raise RuntimeError(f"monday: {json.dumps(r.get('errors') or r.get('error_message'))[:300]}")
    return r["data"]


def setup_monday(board: int) -> dict:
    b = _monday("query ($b: [ID!]) { boards(ids: $b) { name columns { id title type } groups { id title } } }",
                {"b": [board]})["boards"][0]
    cols = {c["title"]: c for c in b["columns"]}
    for title in ("Incident Key", "Order ID", "Test Run"):
        if title not in cols:
            c = _monday("mutation ($b: ID!, $t: String!) { create_column(board_id: $b, title: $t, column_type: text) { id title type } }",
                        {"b": board, "t": title})["create_column"]
            cols[title] = c
    for title in ("Status", "Priority"):
        if title not in cols:
            cols[title] = _monday("mutation ($b: ID!, $t: String!) { create_column(board_id: $b, title: $t, column_type: status) { id title type } }",
                                  {"b": board, "t": title})["create_column"]
    g = next((x for x in b["groups"] if x["title"] == GROUP), None)
    if not g:
        g = _monday("mutation ($b: ID!, $n: String!) { create_group(board_id: $b, group_name: $n) { id title } }",
                    {"b": board, "n": GROUP})["create_group"]
    return {"board_id": str(board), "board_name": b["name"], "group_id": g["id"], "group_name": GROUP,
            "columns": {"status": cols["Status"]["id"], "severity": cols["Priority"]["id"], "key": cols["Incident Key"]["id"],
                        "order": cols["Order ID"]["id"], "run": cols["Test Run"]["id"]},
            "url": f"https://crafting-space.monday.com/boards/{board}"}


def main() -> None:
    live = json.loads((CREDS_ROOT / "data" / "live" / "containers.json").read_text())
    out = {"airtable": setup_airtable(live["airtable"]["base_id"]), "monday": setup_monday(int(live["monday"]["board_id"]))}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "containers.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
