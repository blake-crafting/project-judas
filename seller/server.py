"""Seller agent: a genuine A2A server built with the official a2a-sdk.

Adapted from a2aproject/a2a-samples, samples/python/agents/helloworld
(Apache-2.0). Changes: our own skills/card, structured data in and out,
and a deterministic answer engine (seller/knowledge.py) instead of echo.

Run:  .venv/bin/python -m seller.server
Card: http://127.0.0.1:9101/.well-known/agent-card.json
"""

from __future__ import annotations

import os

import uvicorn
from a2a.helpers import (
    get_data_parts,
    get_message_text,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    TaskState,
)
from starlette.applications import Starlette

from seller import docs_client, knowledge, protocol, ramp_docs
from seller.vendors.ramp import RAMP

HOST = os.environ.get("SELLER_HOST", "127.0.0.1")
PORT = int(os.environ.get("SELLER_PORT", "9101"))
# Public URL advertised in the card (differs from HOST:PORT once deployed).
PUBLIC_URL = os.environ.get("SELLER_PUBLIC_URL", f"http://{HOST}:{PORT}")
# Where evidence comes from: "mcp" (our docs MCP server), "direct" (HTTPS fetch), "off" (fixture only)
EVIDENCE_SOURCE = os.environ.get("EVIDENCE_SOURCE", "mcp").lower()


async def gather_evidence(requirement: dict):
    if EVIDENCE_SOURCE == "mcp":
        return await docs_client.get_evidence(requirement)
    if EVIDENCE_SOURCE == "direct":
        return await ramp_docs.get_snapshot()
    return None, "Live docs disabled (EVIDENCE_SOURCE=off)"


def make_card(name: str, description: str, url: str, skills: list[AgentSkill]) -> AgentCard:
    return AgentCard(
        name=name, description=description, version="0.2.0",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=skills,
    )


def build_agent_card() -> AgentCard:
    return AgentCard(
        name="Ramp Developer Documentation Advisor",
        description=(
            "Hackathon-owned adapter, NOT operated by Ramp. Answers read-only "
            "questions about Ramp's public developer API reference (docs.ramp.com/llms-api.txt) "
            "and returns structured evidence with provenance and limitations."
        ),
        version="0.1.0",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=PUBLIC_URL, protocol_version="1.0")
        ],
        skills=[
            AgentSkill(
                id="answer_ramp_api_questions",
                name="Answer Ramp API questions",
                description="Says whether a requested capability is documented, unsupported, or unknown.",
                tags=["ramp", "api", "expenses", "vendors", "documentation", "procurement"],
                examples=["Does Ramp expose API operations for expense and vendor records?"],
                input_modes=["application/json", "text/plain"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="retrieve_documentation_evidence",
                name="Retrieve documentation evidence",
                description="Returns source type, URL, and verification status for each claim.",
                tags=["evidence", "citations", "documentation"],
                output_modes=["application/json"],
            ),
        ],
    )


def _read_requirement(context: RequestContext) -> dict:
    """Accept either a structured data part or plain text from the buyer."""
    data = [d for d in get_data_parts(context.message.parts) if isinstance(d, dict)]
    if data:
        return data[0]
    return {"question": get_message_text(context.message) or "", "requirements": []}


async def ramp_responder(requirement: dict) -> dict:
    docs, docs_error = await gather_evidence(requirement)
    response = knowledge.answer(requirement, docs, docs_error)
    print(f"[seller] request_id={response['request_id']!r} status={response['status']} "
          f"evidence={response['evidence_mode']} via={EVIDENCE_SOURCE} docs_error={docs_error!r} "
          f"mcp_calls={len(response['tool_calls'])} "
          f"claims={[c['finding'] for c in response['claims']]}", flush=True)
    return response


class SellerAgentExecutor(AgentExecutor):
    """Generic A2A executor: reads the buyer requirement, calls `responder`,
    returns its dict as a `seller_response` data artifact."""

    def __init__(self, responder=ramp_responder) -> None:
        self.responder = responder

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Checking documentation evidence..."),
        )

        response = await self.responder(_read_requirement(context))

        await updater.add_artifact(
            parts=[new_data_part(response, media_type="application/json")],
            name="seller_response",
        )
        await updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message(f"Done: {response.get('status') or response.get('schema', 'ok')}"),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")


def build_routes(card: AgentCard, responder) -> list:
    handler = DefaultRequestHandler(
        agent_executor=SellerAgentExecutor(responder),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")


async def ramp_dispatch(payload: dict) -> dict:
    return await protocol.dispatch(payload, RAMP, legacy=ramp_responder)


def build_app() -> Starlette:
    return Starlette(routes=build_routes(build_agent_card(), ramp_dispatch))


if __name__ == "__main__":
    uvicorn.run(build_app(), host=HOST, port=PORT)
