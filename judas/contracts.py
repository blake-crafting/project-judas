"""Judas application message contracts, carried inside A2A data parts.

These are our schemas, not part of the A2A protocol. A2A itself supplies message_id /
context_id / task_id; Judas sets context_id = evaluation_id.
"""

from __future__ import annotations

import uuid

BUYER_REQUIREMENTS = "judas.buyer_requirements/v1"
VENDOR_RESPONSE = "judas.vendor_response/v1"
COMPARISON = "judas.comparison/v1"
ERROR = "judas.error/v1"
DEMO_REQUEST = "judas.demo_request/v1"       # buyer asks a seller to demonstrate a capability
DEMO_RESULT = "judas.demo_result/v1"         # seller's executed demonstration (tool calls + observations)
FOLLOW_UP = "judas.follow_up/v1"              # buyer challenges one unresolved requirement
FOLLOW_UP_RESULT = "judas.follow_up_result/v1"  # seller's broader lookup for that requirement
SCHEMAS = frozenset({BUYER_REQUIREMENTS, VENDOR_RESPONSE, COMPARISON, ERROR, DEMO_REQUEST, DEMO_RESULT,
                     FOLLOW_UP, FOLLOW_UP_RESULT, "judas.operation_request/v1", "judas.operation_result/v1"})
QUESTION_IDS = frozenset(f"Q{i}" for i in range(1, 8))
OPERATION_REQUEST = "judas.operation_request/v1"   # buyer asks a seller to perform real product operations
OPERATION_RESULT = "judas.operation_result/v1"     # seller reports what it executed, with real object ids
# Only these hosts count as vendor-operated MCP servers.
VENDOR_MCP_HOSTS = {"airtable": "mcp.airtable.com", "monday": "mcp.monday.com"}
# Evidence strength, weakest to strongest. Orthogonal to whether a requirement is satisfied.
EVIDENCE_LEVELS = ("vendor_claim", "official_docs", "synthetic_demo", "vendor_demo", "verified_integration")
DEMOS = frozenset({"card_transaction_sync"})

PROVENANCE = frozenset({"official_docs", "real_mcp_response", "simulated_vendor_statement",
                        "buyer_computed", "seldon_wren_qa", "synthetic_fixture"})
PRIORITIES = frozenset({"must", "nice", "skip"})
FINDINGS = frozenset({"documented", "partially_documented", "not_found_in_reviewed_docs", "docs_unavailable"})
VERDICTS_FIXED = frozenset({"no_clear_difference_in_docs", "insufficient_evidence", "additional_testing_required"})
VERDICT_VENDORS = frozenset({"ramp", "brex"})
MAX_CUSTOM_QUESTION = 300

REQUIRED = {
    BUYER_REQUIREMENTS: ("evaluation_id", "buyer", "request", "questions"),
    VENDOR_RESPONSE: ("evaluation_id", "vendor_id", "answers", "limitations"),
    COMPARISON: ("evaluation_id", "rows", "coverage", "verdict", "caveat"),
    ERROR: ("message",),
    DEMO_REQUEST: ("evaluation_id", "demo"),
    DEMO_RESULT: ("evaluation_id", "vendor_id", "demo", "evidence_level", "endpoint", "tool_calls", "observations"),
    FOLLOW_UP: ("evaluation_id", "question_id", "missing"),
    FOLLOW_UP_RESULT: ("evaluation_id", "vendor_id", "question_id", "answer", "investigations"),
    "judas.operation_request/v1": ("evaluation_id", "scenario", "tasks", "update"),
    "judas.operation_result/v1": ("evaluation_id", "vendor_id", "interface", "tools_available", "steps", "created"),
}


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def envelope(schema: str, evaluation_id: str, vendor_id: str | None = None, **fields) -> dict:
    payload = {"schema": schema, "evaluation_id": evaluation_id, "vendor_id": vendor_id,
               "message_id": _id("jm"), "request_id": _id("req"), "limitations": []}
    payload.update(fields)
    return payload


def error_payload(evaluation_id: str | None, message: str) -> dict:
    return envelope(ERROR, evaluation_id or "", message=str(message)[:500])


def valid_verdict(v: object) -> bool:
    if v in VERDICTS_FIXED:
        return True
    return isinstance(v, str) and v.startswith("docs_favor:") and v.split(":", 1)[1] in VERDICT_VENDORS


def validate(payload: dict) -> list[str]:
    """Return a list of problems; empty means valid."""
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    schema = payload.get("schema")
    if schema not in SCHEMAS:
        return [f"unknown schema {schema!r}"]
    errors = [f"missing {k}" for k in REQUIRED[schema] if k not in payload]
    if schema == BUYER_REQUIREMENTS:
        cq = payload.get("custom_question")
        if cq is not None and (not isinstance(cq, str) or len(cq) > MAX_CUSTOM_QUESTION):
            errors.append(f"custom_question must be a string of at most {MAX_CUSTOM_QUESTION} chars")
    if schema == FOLLOW_UP:
        if payload.get("question_id") not in QUESTION_IDS:
            errors.append(f"unknown question {payload.get('question_id')!r}")
        m = payload.get("missing")
        if not isinstance(m, list) or not m or len(m) > 10 or not all(isinstance(x, str) and len(x) <= 200 for x in m):
            errors.append("missing must list 1-10 requirement parts")
    if schema == "judas.operation_result/v1":
        errors += _operation_result_problems(payload)
    if schema == FOLLOW_UP_RESULT:
        if not isinstance(payload.get("investigations"), list):
            errors.append("investigations must be a list")
        payload = {**payload, "answers": [payload.get("answer")]}      # same answer rules as a vendor response
        schema = VENDOR_RESPONSE
    if schema == VENDOR_RESPONSE:
        answers = payload.get("answers")
        if not isinstance(answers, list):
            errors.append("answers must be a list")
            answers = []
        for i, a in enumerate(answers):
            if not isinstance(a, dict):
                errors.append(f"answers[{i}] must be an object")
                continue
            qid = a.get("question_id")
            if a.get("finding") not in FINDINGS:
                errors.append(f"{qid}: invalid finding {a.get('finding')!r}")
            ev = a.get("evidence") or []
            if not isinstance(ev, list):
                errors.append(f"{qid}: evidence must be a list")
                ev = []
            ev = [e for e in ev if isinstance(e, dict)] if all(isinstance(e, dict) for e in ev) else (
                errors.append(f"{qid}: evidence items must be objects") or [])
            for e in ev:
                if e.get("provenance") not in PROVENANCE:
                    errors.append(f"{qid}: invalid provenance {e.get('provenance')!r}")
                if not isinstance(e.get("source", ""), str):
                    errors.append(f"{qid}: evidence source must be a string")
            if a.get("finding") in ("documented", "partially_documented") and not any(
                    e.get("provenance") == "real_mcp_response" for e in ev):
                errors.append(f"{qid}: '{a.get('finding')}' requires real_mcp_response evidence")
    if schema == BUYER_REQUIREMENTS and payload.get("priorities") is not None:
        pr = payload["priorities"]
        if not isinstance(pr, dict) or any(v not in PRIORITIES for v in pr.values()):
            errors.append("priorities must map question ids to must | nice | skip")
    if schema == DEMO_REQUEST and payload.get("demo") not in DEMOS:
        errors.append(f"unknown demo {payload.get('demo')!r}")
    if schema == DEMO_RESULT:
        lvl, ep = payload.get("evidence_level"), payload.get("endpoint")
        if lvl not in EVIDENCE_LEVELS:
            errors.append(f"invalid evidence_level {lvl!r}")
        if not isinstance(ep, dict):
            errors.append("endpoint must be an object")
        elif lvl in ("vendor_demo", "verified_integration") and ep.get("vendor_hosted") is not True:
            errors.append(f"evidence_level {lvl!r} requires a vendor-hosted endpoint")
        if lvl == "synthetic_demo" and payload.get("provenance") != "synthetic_fixture":
            errors.append("a synthetic demo must carry provenance 'synthetic_fixture'")
        if not isinstance(payload.get("observations"), list) or not isinstance(payload.get("tool_calls"), list) \
                or not payload.get("tool_calls"):
            errors.append("a demo result needs executed tool_calls and observations")
    if schema == COMPARISON and not valid_verdict(payload.get("verdict")):
        errors.append(f"invalid verdict {payload.get('verdict')!r}")
    return errors


def _operation_result_problems(p: dict) -> list[str]:
    """A seller may only claim a vendor-operated interface it really used, and only tools that interface listed."""
    from urllib.parse import urlparse
    errs = []
    itf = p.get("interface") if isinstance(p.get("interface"), dict) else {}
    vendor = p.get("vendor_id")
    host = urlparse(str(itf.get("url", ""))).hostname
    if itf.get("vendor_operated") and host != VENDOR_MCP_HOSTS.get(vendor):
        errs.append(f"interface claims vendor-operated but {host!r} is not {vendor}'s official MCP host")
    tools = p.get("tools_available")
    if not isinstance(tools, list):
        errs.append("tools_available must be a list")
        tools = []
    steps = p.get("steps")
    if not isinstance(steps, list):
        errs.append("steps must be a list")
        steps = []
    for st in steps:
        if not isinstance(st, dict) or not isinstance(st.get("tool"), str):
            errs.append("each step needs a tool name")
        elif st["tool"] not in tools:
            errs.append(f"step uses {st['tool']!r}, which the interface did not list")
    if not isinstance(p.get("created"), dict):
        errs.append("created must be an object")
    return errs
