"""Brex seller agent: a genuine A2A server (official a2a-sdk) on :9104.

Simulated Brex representative operated by the hackathon team, NOT by Brex. Technical
answers come only from Brex's public OpenAPI specs via our docs MCP server.

Run:  .venv/bin/python -m seller.vendor_server
"""

from __future__ import annotations

import os

import uvicorn
from a2a.types import AgentSkill
from starlette.applications import Starlette

from seller import protocol
from seller.server import build_routes, make_card
from seller.vendors.brex import BREX

HOST = os.environ.get("BREX_HOST", "127.0.0.1")
PORT = int(os.environ.get("BREX_PORT", "9104"))
PUBLIC_URL = os.environ.get("BREX_PUBLIC_URL", f"http://{HOST}:{PORT}")


def build_card():
    return make_card(
        name="Brex Developer Documentation Advisor",
        description=("Hackathon-owned adapter, NOT operated by Brex. Answers read-only questions about Brex's "
                     "public OpenAPI specs (developer.brex.com) with cited evidence and limitations."),
        url=PUBLIC_URL,
        skills=[AgentSkill(id="answer_brex_api_questions", name="Answer Brex API questions",
                           description="Answers the buyer's question set from Brex's published API docs.",
                           tags=["brex", "api", "expenses", "transactions", "documentation"],
                           input_modes=["application/json"], output_modes=["application/json"])],
    )


async def brex_dispatch(payload: dict) -> dict:
    return await protocol.dispatch(payload, BREX)


def build_app() -> Starlette:
    return Starlette(routes=build_routes(build_card(), brex_dispatch))


if __name__ == "__main__":
    uvicorn.run(build_app(), host=HOST, port=PORT)
