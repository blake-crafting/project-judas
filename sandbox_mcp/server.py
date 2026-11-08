"""Judas Sandbox MCP: OUR read-only MCP server of SYNTHETIC, vendor-shaped card transactions.

Why it exists: Ramp's demo MCP needs a Ramp account token (demo-mcp.ramp.com → 401) and the
candidate public demo URL returns 404, and Brex has no public demo environment. So seller agents
demonstrate integration behavior here instead: real MCP transport, real executable code, synthetic
data. NOTHING here comes from Ramp or Brex. Results prove our adapter logic against synthetic
fixtures shaped like each vendor's published schema; they do not prove either vendor's API
behaves this way.

Both vendors get the SAME underlying scenario (same purchases, same overlapping sync windows),
rendered in each vendor's documented field names and types, so a comparison is fair.

Run:  .venv/bin/python -m sandbox_mcp.server      -> http://127.0.0.1:9105/mcp
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

HOST = os.environ.get("SANDBOX_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("SANDBOX_MCP_PORT", "9105"))
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SHAPE_SOURCES = {
    "ramp": "https://docs.ramp.com/llms-api.txt (GET /developer/v1/transactions)",
    "brex": "https://developer.brex.com/_spec/openapi/transactions_api.yaml (GET /v2/transactions/card/primary)",
}
DISCLAIMER = ("SYNTHETIC data from the hackathon team's sandbox MCP server, shaped like the vendor's published "
              "schema. Not from Ramp or Brex; proves adapter logic only, not vendor API behavior.")
EXPONENT = {"USD": 2, "EUR": 2, "JPY": 0}

# One scenario for both vendors. `seen` lists the sync windows a record appears in; a record in
# two windows is an overlap duplicate. P5 changes state between windows (same id).
SCENARIO: list[dict[str, Any]] = [
    {"key": "P1", "merchant": "Blue Bottle Coffee", "major": "12.34", "currency": "USD", "date": "2026-11-01", "seen": [1]},
    {"key": "P2", "merchant": "Delta Air Lines", "major": "1250.00", "currency": "USD", "date": "2026-11-02", "seen": [1]},
    {"key": "P3", "merchant": "Tokyo Hotel", "major": "15000", "currency": "JPY", "date": "2026-11-03", "seen": [1, 2]},
    {"key": "P4", "merchant": "Berlin Taxi", "major": "89.90", "currency": "EUR", "date": "2026-11-04", "seen": [2]},
    {"key": "P5", "merchant": "AWS", "major": "45.00", "currency": "USD", "date": "2026-11-03", "seen": [1, 2],
     "state_by_window": {1: "PENDING", 2: "CLEARED"}},
    {"key": "P6", "merchant": "Office Depot", "major": "7.25", "currency": "USD", "date": "2026-11-05", "seen": [2]},
]
WINDOWS = {1: ("2026-11-01", "2026-11-03"), 2: ("2026-11-03", "2026-11-05")}


def _minor(major: str, currency: str) -> int:
    exp = EXPONENT[currency]
    whole, _, frac = major.partition(".")
    frac = (frac + "0" * exp)[:exp]
    return int(whole) * 10 ** exp + (int(frac) if frac else 0)


def _ramp(p: dict, window: int) -> dict:
    # Ramp documents `entity_amount` (integer, smallest denomination, with currency) as the current field;
    # `amount` / `currency_code` are deprecated but still in the schema, so they are included too.
    rate = 10 ** EXPONENT[p["currency"]]
    return {"id": f"ramp-tx-{p['key'].lower()}-7f3a",
            "entity_amount": {"value": _minor(p["major"], p["currency"]), "currency": p["currency"]},
            "amount": float(p["major"]), "currency_code": p["currency"],
            "minor_unit_conversion_rate": rate, "merchant_name": p["merchant"],
            "state": p.get("state_by_window", {}).get(window, "CLEARED"),
            "user_transaction_time": f"{p['date']}T10:00:00Z", "settlement_date": p["date"]}


def _brex(p: dict, window: int) -> dict:
    # Brex documents CardTransaction.amount as Money {amount: int64 smallest denomination, currency: ISO 4217}.
    # The primary card endpoint lists settled transactions, so the pending→cleared change shows up as the
    # same id re-appearing in the overlapping window.
    return {"id": f"brex-ctx-{p['key'].lower()}-91c2", "description": p["merchant"],
            "amount": {"amount": _minor(p["major"], p["currency"]), "currency": p["currency"]},
            "initiated_at_date": p["date"], "posted_at_date": p["date"], "type": "PURCHASE"}


SHAPERS = {"ramp": _ramp, "brex": _brex}

mcp = MCPServer(
    name="judas-sandbox",
    title="Judas Sandbox MCP (synthetic vendor-shaped data, operated by the hackathon team)",
    version="0.1.0",
    instructions=DISCLAIMER + " Pass vendor='ramp' or 'brex'.",
)


def _meta(vendor: str) -> dict[str, Any]:
    return {"vendor": vendor, "synthetic": True, "operated_by": "hackathon team (not the vendor)",
            "disclaimer": DISCLAIMER, "shape_source": SHAPE_SOURCES[vendor]}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def sandbox_info(vendor: str = "ramp") -> dict[str, Any]:
    """What this sandbox is, which published schema the synthetic records follow, and the sync windows."""
    if vendor not in SHAPERS:
        raise ValueError("vendor must be 'ramp' or 'brex'")
    return {**_meta(vendor), "windows": {str(k): {"from": a, "to": b} for k, (a, b) in WINDOWS.items()},
            "records_in_scenario": len(SCENARIO)}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def list_card_transactions(vendor: str = "ramp", window: int = 1, cursor: int = 0,
                                 limit: int = 2) -> dict[str, Any]:
    """One page of SYNTHETIC card transactions for a sync window (1 or 2; they overlap on 2026-11-03),
    in the vendor's documented shape. Pass the returned next_cursor to get the next page."""
    if vendor not in SHAPERS:
        raise ValueError("vendor must be 'ramp' or 'brex'")
    if window not in WINDOWS:
        raise ValueError("window must be 1 or 2")
    limit = max(1, min(int(limit), 10))
    rows = [SHAPERS[vendor](p, window) for p in SCENARIO if window in p["seen"]]
    page = rows[cursor: cursor + limit]
    nxt = cursor + limit if cursor + limit < len(rows) else None
    body = {"data": page, "page": {"next": nxt}} if vendor == "ramp" else {"items": page, "next_cursor": nxt}
    return {**_meta(vendor), "window": {"from": WINDOWS[window][0], "to": WINDOWS[window][1]},
            "next_cursor": nxt, "record_count": len(page), "response": body}


if __name__ == "__main__":
    mcp.run("streamable-http", host=HOST, port=PORT)
