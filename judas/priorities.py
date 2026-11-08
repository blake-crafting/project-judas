"""Suggest buyer priorities from the buyer's own words. Rule-based and explainable: every change
cites the words that triggered it, and the buyer reviews (and can override) the result before
anything is asked. No LLM is involved, so nothing is invented."""

from __future__ import annotations

import re

from judas.questions import QUESTIONS

# (question id, priority, pattern). Negations are checked first so "don't need real-time" wins.
RULES: list[tuple[str, str, str]] = [
    ("Q6", "nice", r"(don'?t|do not|no) need (real[- ]?time|webhooks?|instant)|nightly|batch (is|are) fine|daily (sync|export)"),
    ("Q6", "must", r"real[- ]?time|instant(ly)?|immediate(ly)?|webhooks?|push notifications?|as soon as"),
    ("Q5", "must", r"duplicat\w*|de-?dup\w*|idempoten\w*|import(ed)? twice|double[- ]count\w*"),
    ("Q3", "must", r"currenc\w*|multi-?currency|\bfx\b|cents|minor units?|amounts?"),
    ("Q2", "must", r"identifiers?|\bids?\b|traceab\w*|audit trail"),
    ("Q4", "must", r"incremental\w*|only (new|changed)|changed records|delta|since (the )?last (sync|run)"),
    ("Q7", "must", r"read[- ]only|least privilege|no write access|minimal (access|permissions?)"),
]


def suggest(text: str) -> dict:
    base = {q.id: ("must" if q.must_have else "nice") for q in QUESTIONS}
    req = {q.id: q.requirement for q in QUESTIONS}
    low = (text or "")[:2000].lower()
    decided: set[str] = set()
    reasons = []
    for qid, prio, pat in RULES:
        if qid in decided:
            continue
        m = re.search(pat, low)
        if m:
            decided.add(qid)
            if base[qid] != prio or prio == "must":
                reasons.append({"question_id": qid, "requirement": req[qid], "priority": prio, "matched": m.group(0)})
            base[qid] = prio
    return {"priorities": base, "reasons": reasons}
