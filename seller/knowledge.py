"""Seller-side answer engine (deterministic, no LLM).

For each capability the buyer asks about, decide: documented / unsupported /
unknown, and attach evidence with provenance.

Evidence sources, best first:
  1. LIVE official docs: Ramp's public API reference (seller/ramp_docs.py),
     fetched over HTTPS. Claims cite exact operations + required fields.
     source_type="official_docs", verified=True (the operation exists in
     Ramp's published reference), observed_via_tool=False (not an MCP call).
  2. LOCAL FIXTURE: hand-written notes, used only when live docs are
     unavailable. source_type="local_fixture", verified=False, and the
     response says "Live vendor docs unavailable".

"documented" never means "tested": nobody has called Ramp's API here.
"""

from __future__ import annotations

import re

from seller.ramp_docs import DocsSnapshot, Operation

VENDOR_ID = "ramp-docs-adapter"
RAMP_API_DOCS = "https://docs.ramp.com/developer-api/v1"
MAX_EVIDENCE_OPS = 5

# Topic -> trigger keywords, Ramp API path prefixes, and the fallback fixture note.
TOPICS: dict[str, dict] = {
    "vendor records": {
        "keywords": ["vendor", "supplier"],
        "paths": ["/developer/v1/vendors"],
        "fixture": "Fixture note: Ramp's developer API docs describe vendor resources.",
    },
    "expenses": {
        "keywords": ["expense", "transaction", "spend", "card spend"],
        "paths": ["/developer/v1/transactions", "/developer/v1/reimbursements"],
        "fixture": "Fixture note: Ramp's developer API docs describe transaction (card spend) resources.",
    },
    "reimbursements": {
        "keywords": ["reimbursement", "out-of-pocket"],
        "paths": ["/developer/v1/reimbursements"],
        "fixture": "Fixture note: Ramp's developer API docs describe reimbursement resources.",
    },
    "bills": {
        "keywords": ["bill", "accounts payable", "invoice"],
        "paths": ["/developer/v1/bills"],
        "fixture": "Fixture note: Ramp's developer API docs describe bill resources.",
    },
    "webhooks": {
        "keywords": ["webhook", "event subscription"],
        "paths": ["/developer/v1/webhooks"],
        "fixture": None,  # fixture had no evidence for this
    },
    "purchase orders": {
        "keywords": ["purchase order"],
        "paths": ["/developer/v1/purchase-orders"],
        "fixture": None,
    },
    "cards": {
        "keywords": ["virtual card", "physical card", "card program", "cards api"],
        "paths": ["/developer/v1/cards"],
        "fixture": None,
    },
    "users": {
        "keywords": ["user", "employee"],
        "paths": ["/developer/v1/users"],
        "fixture": None,
    },
    "accounting / GL": {
        "keywords": ["general ledger", "gl account", "accounting", "erp sync"],
        "paths": ["/developer/v1/accounting"],
        "fixture": None,
    },
    "API documentation": {
        "keywords": ["api documentation", "api docs", "documentation", "developer api"],
        "paths": [],  # satisfied by the reference itself existing
        "fixture": "Fixture note: Ramp publishes developer API documentation.",
    },
}

# Topics buyers ask about that Ramp's API reference has no section for.
# Detected so a free-text question never silently drops a request.
NO_SECTION_TOPICS: dict[str, list[str]] = {
    "payroll": ["payroll"],
    "HR / benefits": ["benefits", "hris"],
    "tax filing": ["tax filing", "file taxes", "sales tax"],
    "SSO / SCIM provisioning": ["sso", "scim", "saml"],
    "rate limits": ["rate limit"],
}

# Requests asking THIS AGENT to act. Matched as imperative phrases so that a
# docs question ("is there an API for payments / purchase orders?") is fine.
WRITE_ACTION_PATTERNS = [
    r"\bapprove (a|an|the|this|our|my)\b", r"\bpay (a|an|the|this|our|my|\$)", r"\bsend money\b",
    r"\btransfer (money|funds|\$)", r"\bissue (a|an) .*card\b", r"\bmake a purchase\b",
    r"\bbuy (a|an|the|this|it)\b", r"\bwire (money|funds|\$)", r"\bcharge (my|our|the)\b",
]

BASE_LIMITATIONS = ["This adapter is operated by the hackathon team, not by Ramp."]
LIVE_MEANING = ("'documented' means the operation appears in Ramp's published API reference; "
                "no API call was made, so nothing here is 'tested'.")
FIXTURE_MEANING = ("'documented' here only means the hackathon team's fixture note says so; "
                   "it is unverified and nothing here is 'tested'.")


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9$\- ]+", " ", text.lower())
    # crude singularisation so "vendors" matches "vendor"
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
             for w in text.split()]
    return " " + " ".join(words) + " "


def _contains(text_norm: str, keyword: str) -> bool:
    return _norm(keyword) in text_norm


def topics_in(text: str) -> list[str]:
    t = _norm(text)
    return [name for name, spec in TOPICS.items() if any(_contains(t, k) for k in spec["keywords"])]


def _is_write_request(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in WRITE_ACTION_PATTERNS)


def _ops_for(topic: str, docs: DocsSnapshot) -> list[Operation]:
    prefixes = TOPICS[topic]["paths"]
    return [o for o in docs.operations
            if any(o.path == p or o.path.startswith(p + "/") for p in prefixes)]


def _title_search(req: str, docs: DocsSnapshot) -> list[Operation]:
    """Fallback for requirements outside TOPICS: phrase match in operation titles."""
    phrase = _norm(req).strip()
    if len(phrase) < 4:
        return []
    return [o for o in docs.operations if phrase in _norm(o.title)]


STOPWORDS = set("a an and the of for to in on by with our we us you your my do doe does is are "
                "can could ramp api expose operation record show exact documented its it "
                "required field me what which how".split())


def _rank(ops: list[Operation], question: str) -> list[Operation]:
    """Prefer titles sharing meaningful words with the question, then the
    simplest (shortest) path, e.g. 'List vendors' over sub-resources."""
    q = set(_norm(question).split()) - STOPWORDS
    return sorted(ops, key=lambda o: (-len(q & set(_norm(o.title).split())),
                                      o.path.count("/"), len(o.path), o.title))


def _claim(req: str, finding: str, detail: str, **kw) -> dict:
    base = {"requirement": req, "finding": finding, "detail": detail, "source_type": None,
            "source_url": None, "observed_via_tool": False, "verified": False, "evidence": []}
    return {**base, **kw}


def _live_claim(req: str, topic: str | None, docs: DocsSnapshot, question: str) -> dict:
    prov = {"source_type": "official_docs", "source_url": docs.source_url,
            "retrieved_at": docs.retrieved_at, "observed_via_tool": docs.via is not None,
            "via": docs.via or "direct_https"}
    if topic == "API documentation":
        return _claim(req, "documented",
                      f"Ramp publishes a machine-readable API reference with {docs.total} operations.",
                      verified=True, **prov)
    ops = _ops_for(topic, docs) if topic else _title_search(req, docs)
    if not ops:
        return _claim(req, "unknown", "Not found in Ramp's published API reference.", **prov)
    ranked = _rank(ops, question)
    return _claim(
        req, "documented",
        f"{len(ops)} documented operation(s) in Ramp's API reference, e.g. "
        f"'{ranked[0].title}' ({ranked[0].path}).",
        verified=True, evidence=[o.as_evidence() for o in ranked[:MAX_EVIDENCE_OPS]], **prov,
    )


def _fixture_claim(req: str, topic: str | None) -> dict:
    note = TOPICS[topic]["fixture"] if topic else None
    if note:
        return _claim(req, "documented", note, source_type="local_fixture", source_url=RAMP_API_DOCS)
    return _claim(req, "unknown", "No evidence in the local fixture (live docs unavailable).")


def requirements_to_check(requirement: dict) -> list[str]:
    """Capabilities to evaluate: the buyer's explicit list, else inferred from the question."""
    requested = [str(r)[:100] for r in requirement.get("requirements", [])][:10]
    if requested:
        return requested
    question = str(requirement.get("question", ""))[:1000]
    t = _norm(question)
    return topics_in(question) + [
        name for name, kws in NO_SECTION_TOPICS.items() if any(_contains(t, k) for k in kws)]


def answer(requirement: dict, docs: DocsSnapshot | None = None,
           docs_error: str | None = None) -> dict:
    """Build a structured seller response. `docs=None` -> fixture fallback."""
    question = str(requirement.get("question", ""))[:1000]
    claims: list[dict] = []
    if _is_write_request(question):
        claims.append(_claim(
            "write / financial action", "unsupported",
            "This adapter is read-only documentation Q&A. It cannot approve, pay, "
            "purchase, or change any account.",
            source_type="adapter_policy", verified=True))  # true by construction: our own rule

    for req in requirements_to_check(requirement):
        matched = topics_in(req)
        topic = matched[0] if matched else None
        claims.append(_live_claim(req, topic, docs, question) if docs else _fixture_claim(req, topic))

    unanswered = [c["requirement"] for c in claims if c["finding"] == "unknown"]
    if not claims:
        unanswered.append(question or "(empty question)")

    findings = {c["finding"] for c in claims}
    if findings == {"documented"}:
        status = "answered"
    elif "documented" in findings:
        status = "partially_answered"
    elif findings == {"unsupported"}:
        status = "declined"
    else:
        status = "unanswered"

    limitations = list(BASE_LIMITATIONS) + [LIVE_MEANING if docs else FIXTURE_MEANING]
    if docs:
        evidence_mode = "live_official_docs"
        how = (f"via MCP tools on {docs.via.removeprefix('mcp:')}, the hackathon team's own MCP server (not a Ramp MCP server)"
               if docs.via else "over direct HTTPS, not via an MCP tool")
        limitations.append(
            f"Evidence from {docs.source_url} (retrieved {docs.retrieved_at}"
            + (", served from cache" if docs.from_cache else "") + f"), {how}.")
        if docs_error:
            limitations.append(f"Refresh failed, cache may be stale: {docs_error}")
    else:
        evidence_mode = "local_fixture"
        limitations.insert(0, "Live vendor docs unavailable"
                           + (f" ({docs_error})" if docs_error else "")
                           + ". Evidence comes from a local fixture written by the hackathon team.")

    return {
        "request_id": str(requirement.get("request_id", ""))[:64],
        "vendor_id": VENDOR_ID,
        "status": status,
        "evidence_mode": evidence_mode,
        "claims": claims,
        "unanswered": unanswered,
        "limitations": limitations,
        "tool_calls": docs.tool_calls if docs else [],
    }
