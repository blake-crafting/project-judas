"""Buyer agent: search the curated registry, fetch the seller's Agent Card,
and send a structured question over genuine A2A (official a2a-sdk client).

Every step is recorded in a `trace` list so the UI/CLI can show exactly what
happened. Nothing here is stubbed: if the seller is down, you get an error.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict

import os

REGISTRY_PATH = Path(__file__).with_name("registry.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_registry(path: Path | None = None) -> list[dict]:
    path = path or Path(os.environ.get("REGISTRY_PATH", REGISTRY_PATH))
    return json.loads(path.read_text())["agents"]


def search_registry(query: str, agents: list[dict] | None = None) -> list[dict]:
    """Rank registry entries by how many of their tags/skills appear in the query."""
    agents = agents if agents is not None else load_registry()
    def stems(text: str) -> set[str]:
        # crude singularisation so "expense" matches "expenses"
        return {w[:-1] if len(w) > 3 and w.endswith("s") else w
                for w in re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()}

    q = stems(query)
    results = []
    for a in agents:
        terms = a.get("tags", []) + [s.replace("_", " ") for s in a.get("skills", [])]
        hits = sorted({t for t in terms if stems(t) & q})
        if hits:
            results.append({**a, "score": len(hits), "matched": hits})
    return sorted(results, key=lambda r: -r["score"])


def _allowed_origins(agents: list[dict]) -> set[tuple[str, str, int | None]]:
    out = set()
    for a in agents:
        if a.get("base_url"):
            u = urlparse(a["base_url"])
            out.add((u.scheme, u.hostname, u.port))
    return out


def _check_allowed(url: str, agents: list[dict]) -> None:
    u = urlparse(url)
    if (u.scheme, u.hostname, u.port) not in _allowed_origins(agents):
        raise PermissionError(f"Refusing to contact {url!r}: not in the registry allowlist.")


async def ask_seller(agent_id: str, requirement: dict, agents: list[dict] | None = None) -> dict:
    """Legacy Workflow-1 entry point (plain requirement dict)."""
    return await send_to_seller(agent_id, requirement, None, agents)


async def send_to_seller(agent_id: str, payload: dict, context_id: str | None = None,
                         agents: list[dict] | None = None) -> dict:
    """Run one real buyer -> seller A2A exchange. A2A context_id threads an evaluation.
    Returns {agent, response, trace}."""
    agents = agents if agents is not None else load_registry()
    entry = next((a for a in agents if a["id"] == agent_id), None)
    if entry is None:
        raise KeyError(f"Unknown agent id {agent_id!r}")
    if not entry.get("base_url"):
        raise RuntimeError(f"{entry['name']} has no running endpoint (fictional demo entry).")

    trace: list[dict] = []

    def step(kind: str, **detail) -> None:
        trace.append({"t": _now(), "step": kind, **detail})

    base_url = entry["base_url"]
    _check_allowed(base_url, agents)

    async with httpx.AsyncClient(timeout=15) as http:
        # 1. Discovery: fetch the public Agent Card from /.well-known/agent-card.json
        t0 = time.perf_counter()
        card = await A2ACardResolver(httpx_client=http, base_url=base_url).get_agent_card()
        step("agent_card_fetched", url=f"{base_url}/.well-known/agent-card.json",
             card_name=card.name, skills=[s.id for s in card.skills],
             ms=round((time.perf_counter() - t0) * 1000))

        # 2. Validate the card: the endpoint it advertises must also be allowlisted.
        endpoints = [i.url for i in card.supported_interfaces]
        for ep in endpoints:
            _check_allowed(ep, agents)
        step("agent_card_validated", advertised_endpoints=endpoints)

        # 3. Send the structured requirement as an A2A message (JSON-RPC over HTTP).
        client = await create_client(agent=card, client_config=ClientConfig(streaming=False, httpx_client=http))
        message = new_data_message(payload, media_type="application/json", context_id=context_id,
                                   role=Role.ROLE_USER)
        step("question_sent", method="SendMessage", message_id=message.message_id,
             context_id=context_id, payload=payload)

        t0 = time.perf_counter()
        response: dict | None = None
        task_info: dict = {}
        async for chunk in client.send_message(SendMessageRequest(message=message)):
            raw = MessageToDict(chunk)
            task = getattr(chunk, "task", None) if chunk.HasField("task") else None
            if task is not None:
                task_info = {"task_id": task.id, "state": raw["task"]["status"]["state"]}
                for art in task.artifacts:
                    for d in get_data_parts(art.parts):
                        if isinstance(d, dict):
                            response = d
        await client.close()

        step("seller_answer_received", ms=round((time.perf_counter() - t0) * 1000),
             **task_info, status=(response or {}).get("status"))

    if response is None:
        raise RuntimeError("Seller replied but returned no structured seller_response artifact.")
    return {"agent": entry, "response": _restore_ints(response), "trace": trace}


def _restore_ints(x):
    """A2A data parts use protobuf Struct, which stores all numbers as floats."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, dict):
        return {k: _restore_ints(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_restore_ints(v) for v in x]
    return x


def new_requirement(question: str, requirements: list[str] | None = None,
                    buyer: str = "Fictional Northstar Engineering") -> dict:
    return {
        "request_id": f"req-{uuid.uuid4().hex[:8]}",
        "buyer": buyer,
        "question": question,
        "requirements": requirements or [],
        "consent_to_share": True,
    }
