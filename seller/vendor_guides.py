"""Official vendor GUIDES (prose docs and non-endpoint specs), per vendor, read-only.

The API references (vendor_docs.py) say which endpoints and fields exist. Guides say how
they behave: money units, scopes, pagination, webhook events and delivery. Several Judas
findings were wrong or incomplete because only the API references were reviewed, so a
broader official-source lookup now happens before a requirement is called "not found".

Only the URLs in GUIDES are ever fetched (allowlist). Responses are size-capped, cached
for CACHE_TTL_S, and fall back to a stale cache if a refresh fails. Text is untrusted data:
split into passages and searched, never executed or obeyed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass

import httpx
import yaml

from seller import ramp_docs

GUIDES: dict[str, list[str]] = {
    "ramp": [
        "https://docs.ramp.com/llms-guides/monetary-values.txt",
        "https://docs.ramp.com/llms-guides/authorization.txt",
        "https://docs.ramp.com/llms-guides/pagination.txt",
        "https://docs.ramp.com/llms-guides/webhooks.txt",
        # Canonical spec: the only source with transaction query params and field descriptions
        # (llms-api.txt omits both). Only transaction-related parts are kept (see JSON_KEEP).
        "https://docs.ramp.com/openapi/developer-api.json",
    ],
    "brex": [
        "https://developer.brex.com/_spec/openapi/webhooks_api.yaml",
        "https://developer.brex.com/guides/pagination.md",
        "https://developer.brex.com/guides/roles_permissions_scopes.md",
        "https://developer.brex.com/guides/idempotency.md",
        "https://developer.brex.com/guides/webhooks.md",
    ],
}
# Big JSON specs: keep passages whose path mentions one of these (bounded, relevant).
JSON_KEEP = ("/developer/v1/transactions", "Transaction", "ApiSignedAmount", "CurrencyAmount")
MAX_PASSAGE = 1200
_refresh_failed_at: dict[str, float] = {}


@dataclass
class Passage:
    vendor: str
    source_url: str
    title: str
    text: str


@dataclass
class GuideSet:
    vendor: str
    passages: list[Passage]
    retrieved_at: str
    from_cache: bool
    sources: list[str]


# ---------- parsing ----------

def _split_markdown(text: str, url: str, vendor: str) -> list[Passage]:
    out: list[Passage] = []
    title, buf = url.rsplit("/", 1)[-1], []
    def flush():
        body = " ".join(" ".join(buf).split())
        while body:
            out.append(Passage(vendor, url, title[:160], body[:MAX_PASSAGE]))
            body = body[MAX_PASSAGE:]
    for line in text.splitlines():
        m = re.match(r"^\s{0,3}#{1,4}\s+(.+)$", line)
        if m:
            flush()
            title, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    flush()
    return out


def _walk_yaml(node, path: str, url: str, vendor: str, out: list[Passage], depth: int = 0,
               keep: tuple[str, ...] = (), cap: int = 600) -> None:
    if depth > 16 or len(out) > cap:
        return
    if isinstance(node, dict):
        bits = []
        if isinstance(node.get("description"), str):
            bits.append(node["description"])
        if isinstance(node.get("enum"), list):
            bits.append("enum: " + ", ".join(str(e) for e in node["enum"][:60]))
        if isinstance(node.get("summary"), str):
            bits.append(node["summary"])
        if bits and (not keep or any(k in path for k in keep)):
            out.append(Passage(vendor, url, path[-160:] or "spec", " ".join(" ".join(bits).split())[:MAX_PASSAGE]))
        for k, v in node.items():
            if k not in ("description", "summary", "enum"):
                _walk_yaml(v, f"{path}/{k}" if path else str(k), url, vendor, out, depth + 1, keep, cap)
    elif isinstance(node, list):
        for i, v in enumerate(node[:200]):
            _walk_yaml(v, f"{path}[{i}]", url, vendor, out, depth + 1, keep, cap)


def parse_guide(text: str, url: str, vendor: str) -> list[Passage]:
    if url.endswith(".json"):
        try:
            spec = json.loads(text)
        except ValueError:
            return []
        out: list[Passage] = []
        for k in ("paths", "components"):
            _walk_yaml(spec.get(k) or {}, k, url, vendor, out, keep=JSON_KEEP, cap=3000)
        # parameters carry their name separately from the description; fold it in
        for path, item in ((spec.get("paths") or {}).items()):
            if path.startswith("/developer/v1/transactions") and isinstance(item, dict):
                for method, op in item.items():
                    for prm in (op.get("parameters") or []) if isinstance(op, dict) else []:
                        if isinstance(prm, dict) and prm.get("name"):
                            out.append(Passage(vendor, url, f"{method.upper()} {path} ?{prm['name']}",
                                               f"query parameter {prm['name']}: "
                                               + " ".join(str(prm.get("description") or "").split())[:MAX_PASSAGE]))
        return out
    if url.endswith((".yaml", ".yml")):
        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError:
            return []
        out: list[Passage] = []
        _walk_yaml(spec, "", url, vendor, out)
        return out
    return _split_markdown(text, url, vendor)


# ---------- fetch + cache ----------

def _paths(vendor: str):
    d = ramp_docs.CACHE_DIR
    return [d / f"guides-{vendor}-{i}.txt" for i in range(len(GUIDES[vendor]))], d / f"guides-{vendor}.meta.json"


def _read_cache(vendor: str):
    bodies, meta = _paths(vendor)
    if meta.exists() and all(b.exists() for b in bodies):
        m = json.loads(meta.read_text())
        if m.get("urls") == GUIDES[vendor]:
            return [b.read_text() for b in bodies], m
    return None


def _write_cache(vendor: str, texts: list[str], retrieved_at: str) -> None:
    bodies, meta = _paths(vendor)
    meta.parent.mkdir(parents=True, exist_ok=True)
    for b, t in zip(bodies, texts):
        b.write_text(t)
    meta.write_text(json.dumps({"retrieved_at": retrieved_at, "epoch": time.time(), "urls": GUIDES[vendor]}))


async def _download(url: str, timeout: float) -> str:
    if not any(url in urls for urls in GUIDES.values()):
        raise PermissionError(f"{url} is not an allowlisted vendor guide URL")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http:
        async with http.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            async for part in r.aiter_bytes():
                buf.extend(part)
                if len(buf) > ramp_docs.MAX_BYTES:
                    raise ValueError("vendor guide exceeded size cap")
            return buf.decode("utf-8", errors="replace")


def _build(vendor: str, texts: list[str], retrieved_at: str, from_cache: bool) -> GuideSet:
    passages: list[Passage] = []
    for url, text in zip(GUIDES[vendor], texts):
        passages += parse_guide(text, url, vendor)
    return GuideSet(vendor, passages, retrieved_at, from_cache, list(GUIDES[vendor]))


async def get_guides(vendor: str, force_refresh: bool = False) -> tuple[GuideSet | None, str | None]:
    if vendor not in GUIDES:
        return None, f"unknown vendor '{vendor}'"
    cached = _read_cache(vendor)
    if cached and not force_refresh and time.time() - cached[1].get("epoch", 0) < ramp_docs.CACHE_TTL_S:
        return _build(vendor, cached[0], cached[1]["retrieved_at"], True), None
    failed = _refresh_failed_at.get(vendor)
    if cached and not force_refresh and failed and time.time() - failed < ramp_docs.REFRESH_BACKOFF_S:
        return _build(vendor, cached[0], cached[1]["retrieved_at"], True), "refresh skipped after a recent failure"
    try:
        timeout = ramp_docs.CACHED_TIMEOUT_S if cached else ramp_docs.TIMEOUT_S
        texts = [await _download(u, timeout) for u in GUIDES[vendor]]
        gs = _build(vendor, texts, ramp_docs._now(), False)
        if not gs.passages:
            raise ValueError("guides parsed to zero passages; format may have changed")
        _write_cache(vendor, texts, gs.retrieved_at)
        _refresh_failed_at.pop(vendor, None)
        return gs, None
    except Exception as e:
        _refresh_failed_at[vendor] = time.time()
        err = f"{type(e).__name__}: {e}"[:300]
        if cached:
            return _build(vendor, cached[0], cached[1]["retrieved_at"], True), err
        return None, err


def search(gs: GuideSet, phrase: str, limit: int = 10) -> list[dict]:
    """Passages containing ALL words of `phrase` (case-insensitive), with a snippet around the first."""
    words = [w for w in re.findall(r"[a-z0-9_.:\-]+", phrase.lower()) if len(w) >= 2][:6]
    if not words:
        return []
    hits = []
    for p in gs.passages:
        low = p.text.lower()
        if all(w in low for w in words):
            i = low.find(words[0])
            snippet = " ".join(p.text[max(0, i - 160): i + 260].split())
            hits.append({**asdict(p), "text": p.text[:MAX_PASSAGE], "snippet": snippet})
            if len(hits) >= limit:
                break
    return hits
