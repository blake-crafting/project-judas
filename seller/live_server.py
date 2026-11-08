"""Live seller adapters (official a2a-sdk servers) for the real-vendor scenario.

Hackathon-owned adapters, NOT operated by Airtable or monday.com. On a judas.operation_request they
perform real operations through the vendor's OFFICIAL hosted MCP server and report the real object ids.

Run:  .venv/bin/python -m seller.live_server airtable    (:9106)
      .venv/bin/python -m seller.live_server monday      (:9107)
"""

from __future__ import annotations

import os
import sys

import uvicorn
from a2a.types import AgentSkill
from starlette.applications import Starlette

from judas.contracts import error_payload, validate
from seller.live_ops import run_operations
from seller.server import build_routes, make_card

LIVE = {"airtable": ("Airtable", 9106), "monday": ("monday.com", 9107)}


def build_app(vendor: str) -> Starlette:
    display, port = LIVE[vendor]
    url = os.environ.get(f"{vendor.upper()}_LIVE_PUBLIC_URL", f"http://127.0.0.1:{port}")
    card = make_card(
        name=f"{display} Live Operations Adapter",
        description=(f"Hackathon-owned adapter, NOT operated by {display}. Performs requested project-tracking "
                     f"operations in the buyer's {display} test workspace through {display}'s official hosted MCP "
                     "server, and reports real object ids for independent verification."),
        url=url,
        skills=[AgentSkill(id=f"demonstrate_{vendor}_project_tracking", name=f"Demonstrate {display} project tracking",
                           description="Create a project, tasks with status and priority, and a status update.",
                           tags=[vendor, "project tracking", "tasks", "mcp", "live"],
                           input_modes=["application/json"], output_modes=["application/json"])])

    async def dispatch(payload: dict) -> dict:
        eid = payload.get("evaluation_id") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("schema") != "judas.operation_request/v1":
            return error_payload(eid, "this adapter accepts only judas.operation_request/v1")
        problems = validate(payload)
        if problems:
            return error_payload(eid, "invalid operation_request: " + "; ".join(problems))
        return await run_operations(payload, vendor, display)

    return Starlette(routes=build_routes(card, dispatch))


if __name__ == "__main__":
    v = sys.argv[1] if len(sys.argv) > 1 else ""
    if v not in LIVE:
        raise SystemExit("usage: python -m seller.live_server airtable|monday")
    uvicorn.run(build_app(v), host="127.0.0.1", port=LIVE[v][1])
