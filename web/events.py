"""Append-only event log (JSON Lines). Events are never edited or deleted,
except by the explicit "reset demo data" action.

Each event: event_id, session_id, timestamp, agent_id, event_type, payload.
Payloads are bounded and contain only what the buyer explicitly sent or the
seller explicitly returned. No personal data, no inferred intent.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

EVENTS_PATH = Path(os.environ.get("EVENTS_PATH", Path(__file__).resolve().parents[1] / "data" / "events.jsonl"))
EVENT_TYPES = {
    "buyer_requirement_submitted", "registry_searched", "agent_discovered", "seller_connected",
    "question_sent", "seller_answer_received", "evidence_retrieved", "evidence_unavailable",
    "evaluation_completed", "exchange_failed", "vendor_profile_updated", "evaluation_rerun",
    "evaluation_started", "vendor_contacted", "vendor_response_received", "comparison_completed",
    "demo_requested", "demo_result_received", "demo_failed", "follow_up_sent", "follow_up_received",
    "live_operation_requested", "live_operation_result", "live_operation_failed", "live_verification",
    "vendor_agent_contacted", "crafting_test_started", "crafting_test_result",
    "follow_up_failed",
}
_lock = asyncio.Lock()


def _bound(x, depth: int = 0):
    if depth > 6:
        return "…"
    if isinstance(x, str):
        return x[:500]
    if isinstance(x, list):
        return [_bound(v, depth + 1) for v in x[:20]]
    if isinstance(x, dict):
        return {str(k)[:60]: _bound(v, depth + 1) for k, v in list(x.items())[:30]}
    return x


async def record(session_id: str, event_type: str, payload: dict, agent_id: str | None = None,
                 timestamp: str | None = None) -> dict:
    assert event_type in EVENT_TYPES, event_type
    event = {
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "session_id": session_id[:64],
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "agent_id": agent_id,
        "event_type": event_type,
        "payload": _bound(payload),
    }
    async with _lock:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open("a") as f:
            f.write(json.dumps(event) + "\n")
    return event


def read(session_id: str | None = None) -> list[dict]:
    if not EVENTS_PATH.exists():
        return []
    out = []
    for line in EVENTS_PATH.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session_id is None or e.get("session_id") == session_id:
            out.append(e)
    return out


async def reset() -> None:
    async with _lock:
        if EVENTS_PATH.exists():
            EVENTS_PATH.unlink()
