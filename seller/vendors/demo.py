"""Seller-side capability demonstration, requested by the buyer over A2A.

The seller connects to the Judas sandbox MCP server (synthetic, vendor-shaped data operated by
the hackathon team), and runs a real incremental-sync routine against it: page through two
overlapping sync windows with the vendor's cursor style, de-duplicate by id, normalize amounts
to integer minor units from the vendor's documented current field.

Evidence level: synthetic_demo. It proves the integration logic works on data shaped like the
vendor's published schema. It does NOT prove the vendor's API behaves this way (e.g. that ids
stay stable from pending to cleared in production).
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from mcp import Client

from judas.contracts import DEMO_RESULT, envelope

SANDBOX_MCP_URL = os.environ.get("SANDBOX_MCP_URL", "http://127.0.0.1:9105/mcp")
TOOLS = {"sandbox_info", "list_card_transactions"}
AMOUNT_FIELD = {"ramp": "entity_amount.value (integer, smallest unit)", "brex": "amount.amount (Money, int64 smallest unit)"}


def _amount(vendor: str, rec: dict) -> tuple[int, str]:
    if vendor == "ramp":
        ea = rec.get("entity_amount") or {}
        return int(ea["value"]), str(ea["currency"])
    m = rec.get("amount") or {}
    return int(m["amount"]), str(m["currency"])


def _page(vendor: str, body: dict) -> list[dict]:
    return list((body.get("data") if vendor == "ramp" else body.get("items")) or [])


async def run_demo(request: dict, vendor: str, display: str, url: str | None = None) -> dict:
    url = url or SANDBOX_MCP_URL
    eid = request.get("evaluation_id", "")
    calls: list[dict] = []
    if urlparse(url).scheme not in ("http", "https"):
        raise RuntimeError("bad sandbox MCP URL")
    async with Client(url) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        bad = [n for n in TOOLS if n not in tools or not (tools[n].annotations and tools[n].annotations.read_only_hint)]
        if bad:
            raise RuntimeError(f"sandbox MCP lacks read-only tools {bad}")

        async def call(tool: str, args: dict) -> dict:
            t0 = time.perf_counter()
            r = await client.call_tool(tool, args)
            sc = r.structured_content
            if r.is_error or not isinstance(sc, dict):
                raise RuntimeError(f"{tool} failed")
            calls.append({"tool": tool, "args": args, "records": sc.get("record_count"),
                          "ms": round((time.perf_counter() - t0) * 1000)})
            return sc

        info = await call("sandbox_info", {"vendor": vendor})
        fetched, per_window, sample = [], {}, None
        for window in (1, 2):
            cursor, pages = 0, 0
            while cursor is not None and pages < 10:
                sc = await call("list_card_transactions", {"vendor": vendor, "window": window, "cursor": cursor, "limit": 2})
                rows = _page(vendor, sc.get("response") or {})
                sample = sample or (rows[0] if rows else None)
                fetched += [(window, r) for r in rows]
                per_window[window] = per_window.get(window, 0) + len(rows)
                cursor, pages = sc.get("next_cursor"), pages + 1

    unique: dict[str, dict] = {}
    seen_twice, state_changes = [], []
    for window, r in fetched:                       # later windows win: keep the latest copy of each id
        rid = str(r.get("id"))
        if rid in unique:
            seen_twice.append(rid)
            if r.get("state") and unique[rid].get("state") != r.get("state"):
                state_changes.append(f"{unique[rid].get('state')} → {r.get('state')}")
        unique[rid] = r
    normalized = []
    for r in unique.values():
        minor, cur = _amount(vendor, r)
        normalized.append({"id": r.get("id"), "minor_units": minor, "currency": cur})
    jpy = next((n for n in normalized if n["currency"] == "JPY"), None)
    usd = next((n for n in normalized if n["currency"] == "USD"), None)

    obs = [
        {"requirements": ["Q4"], "text": (f"Paged two overlapping sync windows with {display}-style cursors: "
                                          f"{per_window.get(1, 0)} + {per_window.get(2, 0)} records over "
                                          f"{sum(1 for c in calls if c['tool'] == 'list_card_transactions')} MCP calls.")},
        {"requirements": ["Q5"], "text": (f"The overlap returned {len(seen_twice)} record(s) twice with the same id; "
                                          f"buyer-side de-dup by id kept {len(unique)} unique records (latest copy wins)."
                                          + (f" One changed state ({state_changes[0]}) under the same id in the synthetic data."
                                             if state_changes else ""))},
        {"requirements": ["Q3"], "text": (f"Normalized every amount to integer minor units from {AMOUNT_FIELD[vendor]}"
                                          + (f", e.g. USD {usd['minor_units']} and JPY {jpy['minor_units']} (JPY has no decimals)"
                                             if usd and jpy else "") + ".")},
    ]
    return envelope(DEMO_RESULT, eid, vendor, demo="card_transaction_sync", evidence_level="synthetic_demo",
                    provenance="synthetic_fixture", endpoint={"url": url, "operated_by": info.get("operated_by"),
                                                              "vendor_hosted": False},
                    shape_source=info.get("shape_source"), disclaimer=info.get("disclaimer"),
                    tool_calls=calls, observations=obs, sample_record=sample,
                    normalized_sample=normalized[:3], unique_records=len(unique), duplicates_removed=len(seen_twice),
                    limitations=[f"Synthetic data from the hackathon team's sandbox, shaped like {display}'s published "
                                 "schema. Not from " + display + ".",
                                 f"Proves the sync logic works on this data; does not prove {display}'s API keeps ids "
                                 "stable or returns these records."])
