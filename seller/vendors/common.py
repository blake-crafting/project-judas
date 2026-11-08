"""Doc-backed answer builder for simulated vendor sellers.

Every answer comes from a read-only lookup in the vendor's official docs via our docs
MCP server. 'documented' always carries the evidence; nothing found means
'not_found_in_reviewed_docs' (never 'unsupported'); MCP/docs failure means
'docs_unavailable'. Nothing here is tested against a vendor's real API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from judas.contracts import FOLLOW_UP_RESULT, VENDOR_RESPONSE, envelope
from seller.docs_client import DocsSession, McpEvidenceError

VIA = "hackathon-docs MCP (our wrapper, not the vendor's)"
CUSTOM_ID = "QX"
STOPWORDS = frozenset("""which about their there support supports supported would could should these those where
what does with from your have using into they them this that when will been being able more most
expense expenses transaction transactions import imports imported repeated repeat record records
system systems internal finance financial company vendor vendors provide provides please question""".split())


@dataclass(frozen=True)
class Lookup:
    tool: str                     # list_operations | search_description | get_schema
    arg: str
    pick: tuple[str, ...] = ()
    within: str = ""              # search_description only: restrict to this API path prefix


@dataclass(frozen=True)
class Facet:
    """One separately-checked part of a requirement, answered by one official-source lookup.

    kind says what sort of fact it is, so a recommended pattern is never mistaken for a
    guarantee: native_capability | api_primitive | guarantee | integration_pattern |
    delivery_semantics | scope | deprecation.
    tool: op | field | schema | search (API reference) | guide (official guides / full specs).
    require / require_title: regexes a hit must match, so an unrelated passage that merely
    shares words never counts.
    """
    name: str
    kind: str
    tool: str
    arg: str
    pick: tuple[str, ...] = ()
    within: str = ""
    core: bool = True
    require: str = ""
    require_title: str = ""
    alt: tuple[str, ...] = ()     # extra phrases tried when the buyer challenges this facet


@dataclass(frozen=True)
class VendorProfile:
    vendor_id: str
    display_name: str
    persona: str
    lookups: dict[str, Lookup] = field(default_factory=dict)
    facets: dict[str, tuple[Facet, ...]] = field(default_factory=dict)


def _op_ref(op: dict) -> str:
    return f"{op.get('method')} {op.get('path')}".strip() if op.get("method") else str(op.get("path", ""))


def _evidence(tool: str, res: dict, op: dict | None = None, schema: str | None = None,
              fields: dict | None = None) -> dict:
    return {"provenance": "real_mcp_response", "tool": tool,
            "source": (op or {}).get("source_url") or res.get("source_url", ""), "via": VIA,
            "operation": _op_ref(op) if op else None, "schema_ref": schema,
            "fields": fields or {}, "retrieved_at": res.get("retrieved_at", "")}


def _not_found(qid: str, profile: VendorProfile) -> dict:
    return {"question_id": qid, "finding": "not_found_in_reviewed_docs", "evidence": [],
            "answer": (f"Not found in the reviewed {profile.display_name} documentation. "
                       "This does not mean the capability is unsupported.")}


def _documented(qid: str, text: str, ev: dict) -> dict:
    return {"question_id": qid, "finding": "documented", "answer": text, "evidence": [ev]}


async def _search_answer(qid: str, phrase: str, profile: VendorProfile, docs, within: str = "") -> dict | None:
    res = await docs.search(phrase, in_description=True, path_prefix=within or None)
    if not res.get("match_count"):
        return None
    op = res["operations"][0]
    text = f"Docs mention '{phrase}' in {op.get('title')}: \"{op.get('snippet', '')[:240]}\""
    return _documented(qid, text, _evidence("search_operations", res, op))


def _guide_evidence(res: dict, hit: dict) -> dict:
    return {"provenance": "real_mcp_response", "tool": "search_guides", "source": hit.get("source_url", ""),
            "via": VIA, "operation": None, "schema_ref": None, "section": str(hit.get("title", ""))[:160],
            "quote": str(hit.get("snippet", ""))[:300], "fields": {}, "retrieved_at": res.get("retrieved_at", "")}


async def _check_facet(f: Facet, docs) -> dict:
    """Run one facet lookup. Returns {found, summary, evidence}; MCP failures propagate."""
    miss = {"found": False, "summary": "Not found in the reviewed sources.", "evidence": []}
    if f.tool == "guide":
        res = await docs.search_guides(f.arg)
        hits = [h for h in res.get("passages") or []
                if (not f.require or re.search(f.require, str(h.get("text", ""))))
                and (not f.require_title or re.search(f.require_title, str(h.get("title", ""))))]
        if not hits:
            return miss
        h = hits[0]
        return {"found": True, "summary": f"{h.get('title', '')[:80]}: \"{str(h.get('snippet', ''))[:220]}\"",
                "evidence": [_guide_evidence(res, h)]}
    if f.tool == "search":
        res = await docs.search(f.arg, in_description=True, path_prefix=f.within or None)
        ops = [o for o in res.get("operations") or [] if not f.require or re.search(f.require, str(o.get("snippet", "")))]
        if not ops:
            return miss
        o = ops[0]
        return {"found": True, "summary": f"{o.get('title')}: \"{str(o.get('snippet', ''))[:220]}\"",
                "evidence": [_evidence("search_operations", res, o)]}
    if f.tool == "schema":
        res = await docs.get_schema(f.arg)
        flds = (res.get("schema") or {}).get("fields") or {}
        chosen = {k: v for k, v in flds.items() if not f.pick or k in f.pick}
        if not res.get("found") or not chosen:
            return miss
        ev = _evidence("get_schema", res, schema=f.arg, fields=chosen)
        ev["source"] = (res.get("schema") or {}).get("source_url") or ev["source"]
        return {"found": True, "summary": f"{f.arg}: " + "; ".join(f"{k}: {v}" for k, v in list(chosen.items())[:4]),
                "evidence": [ev]}
    res = await docs.list_operations(f.arg)                       # op | field
    ops = res.get("operations") or []
    if not ops:
        return miss
    op = next((o for o in ops if o.get("path") == f.arg), ops[0])
    if f.tool == "op":
        return {"found": True, "summary": f"{_op_ref(op)} ({op.get('title')})",
                "evidence": [_evidence("list_operations", res, op)]}
    entries = {}
    for fld in (op.get("response_fields") or []) + (op.get("query_params") or []):
        nm = fld.split(":")[0].rstrip("?").strip()
        if nm in f.pick and nm not in entries:
            entries[nm] = fld
    if not entries:
        return miss
    return {"found": True, "summary": f"{op.get('title')} ({_op_ref(op)}): " + "; ".join(entries.values()),
            "evidence": [_evidence("list_operations", res, op, fields=entries)]}


async def investigate(qid: str, facets: tuple[Facet, ...], profile: VendorProfile, docs) -> dict:
    """Check every facet; the finding comes from the core facets only.

    all core found -> documented · some facet found -> partially_documented · none -> not found.
    """
    results = []
    for f in facets:
        r = await _check_facet(f, docs)
        results.append({"name": f.name, "kind": f.kind, "core": f.core, **r})
    return _answer_from(qid, results, profile)


def _answer_from(qid: str, results: list[dict], profile: VendorProfile) -> dict:
    core = [r for r in results if r["core"]]
    if core and all(r["found"] for r in core):
        finding = "documented"
    elif any(r["found"] for r in results):
        finding = "partially_documented"
    else:
        return {**_not_found(qid, profile), "facets": results}
    lines = [f"{'✓' if r['found'] else '✗'} {r['name']}" for r in results]
    return {"question_id": qid, "finding": finding, "answer": " · ".join(lines),
            "evidence": [e for r in results for e in r["evidence"]], "facets": results}


def _probe(f: Facet, phrase: str) -> Facet:
    """The same facet with a different search phrase. Endpoint/field/schema facets become a scoped
    text search; the strict require/require_title rules always carry over."""
    if f.tool in ("guide", "search"):
        return replace(f, arg=phrase)
    req = f.require or (rf"\b{re.escape(f.pick[0])}\b" if f.pick else "")
    return replace(f, tool="search", arg=phrase, within=f.arg if f.tool in ("field", "op") else f.within, require=req)


async def _loose(probe: Facet, docs) -> list[dict]:
    """Hits that share the words but fail the strict rule: shown to the buyer, never counted."""
    if probe.tool == "guide":
        res = await docs.search_guides(probe.arg)
        return [{"source": h.get("source_url", ""), "where": str(h.get("title", ""))[:120],
                 "snippet": str(h.get("snippet", ""))[:240], "counted": False} for h in (res.get("passages") or [])[:2]]
    res = await docs.search(probe.arg, in_description=True, path_prefix=probe.within or None)
    return [{"source": o.get("source_url", ""), "where": _op_ref(o)[:120], "snippet": str(o.get("snippet", ""))[:240],
             "counted": False} for o in (res.get("operations") or [])[:2]]


async def build_follow_up(req: dict, profile: VendorProfile, session_factory=DocsSession) -> dict:
    """Buyer challenge on one requirement: re-check it, then search more widely for each missing part.
    A part is resolved only by a hit that passes the same strict rule as before."""
    qid, missing = req.get("question_id"), set(req.get("missing") or [])
    facets = profile.facets.get(qid, ())
    investigations: list[dict] = []
    async with session_factory(profile.vendor_id) as docs:
        fresh = await investigate(qid, facets, profile, docs)
        results = [dict(r) for r in fresh.get("facets") or []]
        for f, r in zip(facets, results):
            if f.name not in missing or r["found"]:
                continue
            searched, candidates, hit = [], [], None
            for phrase in dict.fromkeys((f.arg,) + f.alt):
                probe = _probe(f, phrase)
                searched.append(f"{'official guides' if probe.tool == 'guide' else 'API reference'}: {phrase}")
                got = await _check_facet(probe, docs)
                if got["found"]:
                    hit = got
                    break
                if len(phrase) >= 5:                   # very short phrases ("id") match everything loosely
                    candidates += await _loose(probe, docs)
            if hit:
                r.update(found=True, summary=hit["summary"], evidence=hit["evidence"], via_follow_up=True)
            seen = set()
            uniq = [c for c in candidates if not (c["snippet"] in seen or seen.add(c["snippet"]))]
            investigations.append({"facet": f.name, "kind": f.kind, "resolved": bool(hit), "searched": searched,
                                   "candidates": uniq[:3]})
        calls = list(getattr(docs, "tool_calls", []))
    return envelope(FOLLOW_UP_RESULT, req.get("evaluation_id", ""), profile.vendor_id, question_id=qid,
                    answer=_answer_from(qid, results, profile), investigations=investigations, tool_calls=calls,
                    limitations=[f"Simulated {profile.display_name} representative; every source is "
                                 f"{profile.display_name}'s official documentation. Candidates are shown for review "
                                 "and never counted as evidence."])


async def answer_question(question: dict, profile: VendorProfile, docs) -> dict:
    qid = question.get("id")
    if qid in profile.facets:
        return await investigate(qid, profile.facets[qid], profile, docs)
    lk = profile.lookups.get(qid)
    if lk is None:
        return {"question_id": qid, "finding": "docs_unavailable", "evidence": [],
                "answer": "This adapter has no documentation lookup for this question."}
    if lk.tool == "search_description":
        return await _search_answer(qid, lk.arg, profile, docs, lk.within) or _not_found(qid, profile)
    if lk.tool == "get_schema":
        res = await docs.get_schema(lk.arg)
        fields = (res.get("schema") or {}).get("fields") or {}
        chosen = {k: v for k, v in fields.items() if not lk.pick or k in lk.pick}
        if not res.get("found") or not chosen:
            return _not_found(qid, profile)
        chosen = dict(list(chosen.items())[:8])
        text = f"{lk.arg}: " + "; ".join(f"{k}: {v}" for k, v in chosen.items())
        ev = _evidence("get_schema", res, schema=lk.arg, fields=chosen)
        ev["source"] = (res.get("schema") or {}).get("source_url") or ev["source"]
        return _documented(qid, text, ev)
    # list_operations
    res = await docs.list_operations(lk.arg)
    ops = res.get("operations") or []
    if not ops:
        return _not_found(qid, profile)
    op = next((o for o in ops if o.get("path") == lk.arg), ops[0])
    if not lk.pick:
        text = (f"{res.get('match_count', len(ops))} documented operation(s) under {lk.arg}, "
                f"e.g. {_op_ref(op)} ({op.get('title')}).")
        return _documented(qid, text, _evidence("list_operations", res, op))
    entries = {}
    for f in (op.get("response_fields") or []) + (op.get("query_params") or []):
        name = f.split(":")[0].rstrip("?").strip()
        if name in lk.pick and name not in entries:
            entries[name] = f
    if not entries:
        return _not_found(qid, profile)
    text = f"{op.get('title')} ({_op_ref(op)}): " + "; ".join(entries.values())
    return _documented(qid, text, _evidence("list_operations", res, op, fields=entries))


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z\-]{4,}", text.lower())
    seen: list[str] = []
    for w in words:
        if w not in STOPWORDS and w not in seen:
            seen.append(w)
    return seen[:4]


async def answer_custom(question_text: str, profile: VendorProfile, docs) -> dict:
    """Bounded keyword search: every meaningful keyword must appear in the SAME operation."""
    kws = _keywords(question_text)
    if not kws:
        return _not_found(CUSTOM_ID, profile)
    hits: dict[tuple, dict] | None = None
    first_res: dict | None = None
    for kw in kws:
        res = await docs.search(kw, in_description=True)
        first_res = first_res or res
        ops = {(o.get("title"), o.get("path")): o for o in res.get("operations") or []}
        hits = ops if hits is None else {k: v for k, v in hits.items() if k in ops}
        if not hits:
            return _not_found(CUSTOM_ID, profile)
    op = next(iter(hits.values()))
    text = f"Docs mention {' + '.join(repr(k) for k in kws)} in {op.get('title')}: \"{op.get('snippet', '')[:240]}\""
    return _documented(CUSTOM_ID, text, _evidence("search_operations", first_res or {}, op))


async def build_vendor_response(requirements: dict, profile: VendorProfile, session_factory=DocsSession) -> dict:
    name = profile.display_name
    limitations = [f"Simulated {name} representative operated by the hackathon team, not {name}.",
                   "Answers come from reviewed published documentation; nothing here was tested against "
                   f"{name}'s production API."]
    questions = list(requirements.get("questions") or [])
    custom = requirements.get("custom_question")
    answers: list[dict] = []
    tool_calls: list[dict] = []
    try:
        async with session_factory(profile.vendor_id) as docs:
            for q in questions:
                answers.append(await answer_question(q, profile, docs))
            if custom:
                answers.append(await answer_custom(custom, profile, docs))
            tool_calls = list(getattr(docs, "tool_calls", []))
    except McpEvidenceError as e:
        limitations.insert(0, f"Live vendor docs unavailable ({e})")
        done = {a["question_id"] for a in answers}
        ids = [q.get("id") for q in questions] + ([CUSTOM_ID] if custom else [])
        answers += [{"question_id": qid, "finding": "docs_unavailable", "evidence": [],
                     "answer": "Live vendor docs unavailable; not answered."} for qid in ids if qid not in done]
    return envelope(VENDOR_RESPONSE, requirements.get("evaluation_id", ""), profile.vendor_id,
                    answers=answers, limitations=limitations, tool_calls=tool_calls,
                    pitch={"provenance": "simulated_vendor_statement", "text": profile.persona},
                    custom_question=custom)
