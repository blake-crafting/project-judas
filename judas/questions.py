"""The buyer's standard technical question set (v1). Sellers answer these by id."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    id: str
    requirement: str
    ask: str
    must_have: bool


QUESTIONS: tuple[Question, ...] = (
    Question("Q1", "transaction source", "Which API operation lists settled card transactions?", True),
    Question("Q2", "identifiers", "What stable transaction identifier is documented?", True),
    Question("Q3", "amounts & currency", "How are amounts and currency represented?", True),
    Question("Q4", "incremental sync", "How do we page and fetch only new or changed records?", False),
    Question("Q5", "no duplicates", "What do the docs say about de-duplication or idempotent re-imports?", True),
    Question("Q6", "change notifications", "Are webhooks documented for transaction events?", False),
    Question("Q7", "read-only access", "What authentication / scopes are required for read-only access?", False),
)


def questions_payload() -> list[dict]:
    return [{"id": q.id, "requirement": q.requirement, "ask": q.ask} for q in QUESTIONS]
