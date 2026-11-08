"""Deterministic, evidence-based comparison of vendor responses (buyer-computed).

Semantics (see plan amendments):
- 'documented' = found in the vendor's reviewed published docs, with a citation. Never "tested".
- 'not_found_in_reviewed_docs' never means "unsupported".
- 'documented_needs_clarification' = documented, but the docs leave a question the buyer must ask.
- The verdict only describes documentation coverage; it never claims which real integration works better.
"""

from __future__ import annotations

from judas.contracts import COMPARISON, envelope
from judas.questions import QUESTIONS
from seller.vendor_docs import VENDORS
from seller.vendor_guides import GUIDES

CAVEAT = ("Based on reviewed official documentation and, where shown, synthetic sandbox demonstrations. "
          "Missing documentation does not mean a capability is unsupported; a synthetic demo proves our adapter "
          "logic, not the vendor's API; nothing here proves which vendor's real integration works better.")
VENDOR_NAMES = {"ramp": "Ramp", "brex": "Brex"}
MUST_HAVE = [q.id for q in QUESTIONS if q.must_have]   # default priorities
CUSTOM_ID = "QX"


def _official(vendor: str, e: dict) -> bool:
    """Evidence counts only if it is a real MCP response citing the vendor's own official doc URL."""
    return (isinstance(e, dict) and e.get("provenance") == "real_mcp_response"
            and isinstance(e.get("source"), str)
            and e["source"] in VENDORS.get(vendor, {}).get("urls", []) + GUIDES.get(vendor, []))


def _substantiated(a: dict, vendor: str) -> dict:
    """A 'documented' claim without official evidence is never counted (defense in depth)."""
    if not isinstance(a, dict):
        return {"question_id": None, "finding": "docs_unavailable", "evidence": [], "answer": "malformed answer"}
    good = [e for e in (a.get("evidence") or []) if _official(vendor, e)]
    if a.get("finding") == "documented" and not good:
        return {**a, "finding": "docs_unavailable", "evidence": [],
                "answer": f"Unsubstantiated claim (no documentation evidence): {a.get('answer', '')}"}
    return {**a, "evidence": good}


def _answers(resp: dict | None, vendor: str) -> dict[str, dict]:
    if not resp or resp.get("schema") != "judas.vendor_response/v1":
        return {}
    out = {}
    for a in resp.get("answers") or []:
        s = _substantiated(a, vendor)
        if s.get("question_id"):
            out[s["question_id"]] = s
    return out


def _clarification(qid: str, vendor: str, ans: dict) -> str | None:
    """Buyer-side rule for 'found, but ask the vendor': the custom question is matched by keyword search."""
    if ans.get("finding") != "documented":
        return None
    ev = (ans.get("evidence") or [{}])[0]
    name = VENDOR_NAMES[vendor]
    if qid == CUSTOM_ID and ev.get("tool") == "search_operations":
        return f"{name}: matched by keyword search in the docs; confirm with {name} that it answers this question."
    return None


def _facets(ans: dict) -> list[dict]:
    out = []
    for f in ans.get("facets") or []:
        if not isinstance(f, dict):
            continue
        ev = [e for e in f.get("evidence") or [] if isinstance(e, dict)]
        out.append({"name": str(f.get("name", ""))[:120], "kind": str(f.get("kind", ""))[:40],
                    "core": bool(f.get("core")), "found": bool(f.get("found")) and bool(ev),
                    "summary": str(f.get("summary", ""))[:300],
                    "source": (ev[0].get("source") if ev else None),
                    "ref": (ev[0].get("operation") or ev[0].get("schema_ref") or ev[0].get("section")) if ev else None})
    return out


def _cell(qid: str, vendor: str, ans: dict | None, observed: list[str]) -> tuple[dict, str | None]:
    if ans is None:
        return {"status": "docs_unavailable", "finding": "docs_unavailable", "summary": "No answer received.",
                "evidence_ref": None, "source": None, "via": None, "tested": False, "facets": [],
                "evidence_levels": [], "observed": []}, None
    ev = (ans.get("evidence") or [None])[0] or {}
    clar = _clarification(qid, vendor, ans)
    status = ans.get("finding", "docs_unavailable")
    if clar:
        status = "documented_needs_clarification"
    levels = (["official_docs"] if ans.get("evidence") else []) + (["synthetic_demo"] if observed else [])
    ref = ev.get("operation") or ev.get("schema_ref") or ev.get("section")
    return {"status": status, "finding": ans.get("finding"), "summary": str(ans.get("answer", ""))[:400],
            "evidence_ref": ref, "source": ev.get("source"), "via": ev.get("via"), "tested": False,
            "facets": _facets(ans), "evidence_levels": levels, "observed": observed}, clar


KIND_LABEL = {"integration_pattern": "a recommended pattern, not a guarantee", "guarantee": "a guarantee",
              "api_primitive": "an API building block", "native_capability": "a vendor capability",
              "delivery_semantics": "delivery behavior", "scope": "an access scope", "deprecation": "a deprecation",
              "limitation": "a documented limitation"}


def _note(qid: str, cells: dict[str, dict]) -> str | None:
    """Plain-English differences, by facet, labeled with what kind of fact each one is."""
    if any(cells[v]["finding"] == "docs_unavailable" for v in VENDOR_NAMES):
        return None          # unavailable docs are not a finding about either vendor
    found = {v: {f["name"]: f for f in cells[v]["facets"] if f["found"]} for v in VENDOR_NAMES}
    missing = {v: {f["name"] for f in cells[v]["facets"] if not f["found"]} for v in VENDOR_NAMES}
    bits = []
    for v in VENDOR_NAMES:
        other = "brex" if v == "ramp" else "ramp"
        for name, f in found[v].items():
            if name in missing[other] or f["kind"] in ("deprecation", "limitation") or (
                    name not in found[other] and name not in missing[other] and not f["core"]):
                bits.append(f"{VENDOR_NAMES[v]}: {name} ({KIND_LABEL.get(f['kind'], f['kind'])}).")
    gaps = sorted({f["name"] for v in VENDOR_NAMES for f in cells[v]["facets"]
                   if f["core"] and not f["found"] and f["name"] in missing["brex" if v == "ramp" else "ramp"]})
    if gaps:
        bits.append("Not found for either vendor: " + "; ".join(gaps) + ".")
    return " ".join(bits)[:420] or None


def _priorities(raw: dict | None) -> dict[str, str]:
    base = {q.id: ("must" if q.must_have else "nice") for q in QUESTIONS}
    for k, v in (raw or {}).items():
        if k in base and v in ("must", "nice", "skip"):
            base[k] = v
    return base


def _observations(demos: dict | None) -> dict[str, dict[str, list[str]]]:
    """vendor -> qid -> observation texts, from validated synthetic demo results."""
    out: dict[str, dict[str, list[str]]] = {v: {} for v in VENDOR_NAMES}
    for v, d in (demos or {}).items():
        if v not in out or not isinstance(d, dict) or d.get("schema") != "judas.demo_result/v1":
            continue
        for o in d.get("observations") or []:
            if isinstance(o, dict) and isinstance(o.get("text"), str):
                for qid in o.get("requirements") or []:
                    out[v].setdefault(str(qid), []).append(o["text"][:240])
    return out


def _follow_ups(items: list | None) -> dict[tuple[str, str], dict]:
    """(vendor, qid) -> latest follow-up summary shown in the cell."""
    out = {}
    for f in items or []:
        if not isinstance(f, dict):
            continue
        inv = [i for i in f.get("investigations") or [] if isinstance(i, dict)]
        out[(f.get("vendor"), f.get("question_id"))] = {
            "resolved": [i.get("facet") for i in inv if i.get("resolved")],
            "still_missing": [i.get("facet") for i in inv if not i.get("resolved")],
            "searched": sum(len(i.get("searched") or []) for i in inv),
            "candidates": [c for i in inv for c in (i.get("candidates") or []) if isinstance(c, dict)][:3],
            "original_finding": (f.get("original_answer") or {}).get("finding"), "at": f.get("at")}
    return out


def compare(evaluation_id: str, responses: dict[str, dict | None], priorities: dict | None = None,
            demos: dict | None = None, follow_ups: list | None = None) -> dict:
    answers = {v: _answers(responses.get(v), v) for v in VENDOR_NAMES}
    prio = _priorities(priorities)
    obs = _observations(demos)
    fus = _follow_ups(follow_ups)
    qids = [q.id for q in QUESTIONS if prio[q.id] != "skip"]
    reqs = {q.id: q.requirement for q in QUESTIONS}
    if any(CUSTOM_ID in answers[v] for v in VENDOR_NAMES):
        qids.append(CUSTOM_ID)
        reqs[CUSTOM_ID] = "custom question"
        prio[CUSTOM_ID] = "nice"
    rows, clarifications = [], []
    for qid in qids:
        cells = {}
        for v in VENDOR_NAMES:
            cells[v], clar = _cell(qid, v, answers[v].get(qid) if answers[v] else None, obs[v].get(qid, []))
            cells[v]["follow_up"] = fus.get((v, qid))
            if clar:
                clarifications.append({"question_id": qid, "vendor": v, "text": clar})
        rows.append({"question_id": qid, "requirement": reqs[qid], "priority": prio[qid],
                     "must_have": prio[qid] == "must", **cells, "buyer_note": _note(qid, cells)})

    std = [r for r in rows if r["question_id"] != CUSTOM_ID]
    def count(v, st):
        return sum(1 for r in std if r[v]["finding"] == st)
    detail = {v: {"documented": count(v, "documented"), "partial": count(v, "partially_documented"),
                  "not_found": count(v, "not_found_in_reviewed_docs"), "unavailable": count(v, "docs_unavailable"),
                  "of": len(std)} for v in VENDOR_NAMES}
    coverage = {v: f"{detail[v]['documented']}/{len(std)}" for v in VENDOR_NAMES}
    musts = [r for r in std if r["must_have"]]

    missing = [v for v in VENDOR_NAMES if not answers[v]
               or all(r[v]["finding"] == "docs_unavailable" for r in std)]
    gaps = [(r["question_id"], v) for r in musts for v in VENDOR_NAMES if r[v]["finding"] == "docs_unavailable"]
    advantage = {r["question_id"]: v for r in musts for v in VENDOR_NAMES
                 if r[v]["finding"] == "documented"
                 and r["brex" if v == "ramp" else "ramp"]["finding"] in ("partially_documented",
                                                                          "not_found_in_reviewed_docs")}
    unresolved = [r for r in musts if any(r[v]["finding"] != "documented" for v in VENDOR_NAMES)]
    deciding: list[str] = []
    if missing:
        verdict = "insufficient_evidence"
        reason = f"No usable documentation answers from {', '.join(VENDOR_NAMES[v] for v in missing)}."
    elif gaps:
        verdict = "insufficient_evidence"
        reason = ("Documentation unavailable for must-have requirement(s) "
                  + ", ".join(sorted({q for q, _ in gaps})) + " from "
                  + ", ".join(sorted({VENDOR_NAMES[v] for _, v in gaps})) + "; cannot compare.")
    elif advantage and len(set(advantage.values())) == 1:
        v = next(iter(advantage.values()))
        verdict, deciding = f"docs_favor:{v}", sorted(advantage)
        reason = (f"Must-have requirement(s) {', '.join(deciding)} are fully documented only by "
                  f"{VENDOR_NAMES[v]} in the reviewed sources.")
        still = [r["question_id"] for r in unresolved if r["question_id"] not in advantage]
        if still:
            reason += f" Still not fully established for either vendor: {', '.join(still)}."
    elif advantage or unresolved:
        verdict = "additional_testing_required"
        split = " Each vendor leads on a different must-have." if advantage else ""
        reason = ("Must-have requirement(s) " + ", ".join(r["question_id"] for r in unresolved)
                  + " are not fully established by either vendor's reviewed documentation." + split)
    elif abs(detail["ramp"]["documented"] - detail["brex"]["documented"]) >= 2:
        v = max(VENDOR_NAMES, key=lambda x: detail[x]["documented"])
        verdict = f"docs_favor:{v}"
        reason = (f"Both cover every must-have; {VENDOR_NAMES[v]}'s reviewed docs fully cover "
                  f"{abs(detail['ramp']['documented'] - detail['brex']['documented'])} more of the other requirements.")
    else:
        verdict = "no_clear_difference_in_docs"
        reason = "Both vendors' reviewed docs cover the must-have requirements; other coverage differs by less than 2."

    def open_items(r):
        return [f["name"] for v in VENDOR_NAMES for f in r[v]["facets"] if f["core"] and not f["found"]]
    explanation = {
        "mattered": [r["question_id"] for r in musts],
        "unresolved": [{"question_id": r["question_id"], "requirement": r["requirement"],
                        "ramp": r["ramp"]["status"], "brex": r["brex"]["status"],
                        "missing": sorted(set(open_items(r)))} for r in unresolved],
        "could_change": [f"{r['question_id']} ({r['requirement']}): an official statement or a vendor-sandbox test "
                         f"showing {', '.join(sorted(set(open_items(r)))) or 'the missing evidence'}"
                         for r in unresolved][:5],
    }
    return envelope(COMPARISON, evaluation_id, rows=rows, coverage=coverage, coverage_detail=detail,
                    verdict=verdict, verdict_reason=reason, deciding=deciding, explanation=explanation,
                    priorities=prio, clarifications=clarifications, caveat=CAVEAT, provenance="buyer_computed")
