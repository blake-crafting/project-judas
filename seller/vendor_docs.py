"""Official vendor documentation, per vendor, read-only.

- Ramp: https://docs.ramp.com/llms-api.txt (parsed by seller/ramp_docs.py)
- Brex: public OpenAPI 3.0.1 specs (Transactions + Expenses APIs)

Only the URLs in VENDORS are ever fetched (allowlist). Responses are size-capped,
cached for CACHE_TTL_S, and fall back to a stale cache (labeled) if a refresh fails.
Document text is untrusted data: parsed with yaml.safe_load / regexes, never executed.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import yaml

from seller import ramp_docs
from seller.ramp_docs import DocsSnapshot, Operation, SchemaDef

BREX_TX = "https://developer.brex.com/_spec/openapi/transactions_api.yaml"
BREX_EX = "https://developer.brex.com/_spec/openapi/expenses_api.yaml"
VENDORS: dict[str, dict] = {
    "ramp": {"label": "Ramp", "kind": "llms_txt", "urls": [ramp_docs.SOURCE_URL]},
    "brex": {"label": "Brex", "kind": "openapi", "urls": [BREX_TX, BREX_EX]},
}
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_refresh_failed_at: dict[str, float] = {}      # vendor -> time of last failed refresh (backoff)
MAX_REF_DEPTH = 8


# ---------- OpenAPI parsing ----------

def _resolve(node: Any, spec: dict, depth: int = 0) -> Any:
    """Follow local $ref pointers ("#/components/..."), bounded depth."""
    while isinstance(node, dict) and "$ref" in node and depth < MAX_REF_DEPTH:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return {}
        cur: Any = spec
        for part in ref[2:].split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        node, depth = cur, depth + 1
    return node if isinstance(node, (dict, list)) else {}


def _properties(schema: Any, spec: dict, depth: int = 0) -> dict[str, Any]:
    """Merged properties of a schema, resolving $ref and allOf."""
    schema = _resolve(schema, spec)
    if not isinstance(schema, dict) or depth > MAX_REF_DEPTH:
        return {}
    props = dict(schema.get("properties") or {})
    for part in schema.get("allOf") or []:
        props.update(_properties(part, spec, depth + 1))
    return props


def _type_text(prop: Any, spec: dict) -> str:
    raw = prop if isinstance(prop, dict) else {}
    ref_name = raw.get("$ref", "").rsplit("/", 1)[-1] if isinstance(raw.get("$ref"), str) else ""
    if not ref_name and raw.get("allOf"):
        first = raw["allOf"][0]
        ref_name = first.get("$ref", "").rsplit("/", 1)[-1] if isinstance(first, dict) else ""
    p = _resolve(raw, spec) if not ref_name else {}
    typ = ref_name or p.get("type") or ("object" if p.get("properties") else "any")
    if p.get("format"):
        typ = f"{typ} ({p['format']})"
    desc = " ".join(str(p.get("description") or raw.get("description") or "").split())[:160]
    return f"{typ} — {desc}" if desc else str(typ)


def _response_fields(op: dict, spec: dict) -> list[str]:
    ok = (op.get("responses") or {}).get("200") or (op.get("responses") or {}).get("201") or {}
    ok = _resolve(ok, spec)
    schema = (((ok.get("content") or {}).get("application/json") or {}).get("schema")) if isinstance(ok, dict) else None
    if not schema:
        return []
    props = _properties(schema, spec)
    items = props.get("items")
    if isinstance(items, dict) and (_resolve(items, spec).get("type") == "array" or "items" in items):
        props = _properties(_resolve(items, spec).get("items", {}), spec) or props
    return [f"{k}: {_type_text(v, spec).split(' — ')[0]}" for k, v in props.items()][:60]


def parse_openapi(text: str, source_url: str) -> tuple[list[Operation], dict[str, SchemaDef]]:
    spec = yaml.safe_load(text) or {}
    if not isinstance(spec, dict):
        return [], {}
    ops: list[Operation] = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = [_resolve(p, spec) for p in (op.get("parameters") or []) + (item.get("parameters") or [])]
            params = [p for p in params if isinstance(p, dict) and p.get("name")]
            query = [p["name"] for p in params if p.get("in") == "query"]
            body = _resolve((((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema"), spec)
            required = list(body.get("required") or []) if isinstance(body, dict) else []
            text_parts = [str(op.get("summary") or ""), str(op.get("description") or "")]
            text_parts += [f"{p['name']}: {' '.join(str(p.get('description') or '').split())}" for p in params if p.get("description")]
            ops.append(Operation(
                title=" ".join(str(op.get("summary") or op.get("operationId") or f"{method.upper()} {path}").split())[:200],
                path=str(path)[:200],
                description=" ".join(t for t in text_parts if t).strip()[:4000],
                required_fields=[str(r) for r in required][:30],
                method=method.upper(),
                query_params=query[:30],
                response_fields=_response_fields(op, spec),
                source_url=source_url,
            ))
    schemas: dict[str, SchemaDef] = {}
    components = spec.get("components") or {}
    for name, sch in (components.get("schemas") or {}).items():
        props = _properties(sch, spec)
        schemas[name] = SchemaDef(name=name, fields={k: _type_text(v, spec) for k, v in props.items()},
                                  description=" ".join(str((sch or {}).get("description") or "").split())[:300],
                                  source_url=source_url)
    scopes: dict[str, str] = {}
    for scheme in (components.get("securitySchemes") or {}).values():
        for flow in ((scheme or {}).get("flows") or {}).values():
            for scope, desc in ((flow or {}).get("scopes") or {}).items():
                scopes[str(scope)] = str(desc)[:160]
    if scopes:
        schemas["SecurityScopes"] = SchemaDef("SecurityScopes", scopes, "OAuth2 scopes declared by the spec",
                                              source_url=source_url)
    return ops, schemas


# ---------- snapshots + cache ----------

def _cache_paths(vendor: str) -> tuple[list, Any]:
    d = ramp_docs.CACHE_DIR
    return [d / f"{vendor}-{i}.txt" for i in range(len(VENDORS[vendor]["urls"]))], d / f"{vendor}.meta.json"


def _read_cache(vendor: str) -> tuple[list[str], dict] | None:
    bodies, meta = _cache_paths(vendor)
    if meta.exists() and all(b.exists() for b in bodies):
        return [b.read_text() for b in bodies], json.loads(meta.read_text())
    return None


def _write_cache(vendor: str, texts: list[str], retrieved_at: str) -> None:
    bodies, meta = _cache_paths(vendor)
    meta.parent.mkdir(parents=True, exist_ok=True)
    for b, t in zip(bodies, texts):
        b.write_text(t)
    meta.write_text(json.dumps({"retrieved_at": retrieved_at, "epoch": time.time(), "urls": VENDORS[vendor]["urls"]}))


async def _download(url: str, timeout: float | None = None) -> str:
    if not any(url in v["urls"] for v in VENDORS.values()):
        raise PermissionError(f"{url} is not an allowlisted vendor doc URL")
    async with httpx.AsyncClient(timeout=timeout or ramp_docs.TIMEOUT_S, follow_redirects=False) as http:
        async with http.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            async for part in r.aiter_bytes():
                buf.extend(part)
                if len(buf) > ramp_docs.MAX_BYTES:
                    raise ValueError("vendor docs response exceeded size cap")
            return buf.decode("utf-8", errors="replace")


def _snapshot_from(vendor: str, texts: list[str], retrieved_at: str, from_cache: bool) -> DocsSnapshot:
    ops: list[Operation] = []
    schemas: dict[str, SchemaDef] = {}
    for url, text in zip(VENDORS[vendor]["urls"], texts):
        o, s = parse_openapi(text, url)
        ops += o
        for name, sch in s.items():
            if name == "SecurityScopes" and name in schemas:      # merge scopes across specs
                prev = schemas[name]
                schemas[name] = SchemaDef(name, {**prev.fields, **sch.fields}, prev.description,
                                          source_url=prev.source_url,
                                          sources=list(dict.fromkeys(prev.sources + [sch.source_url])))
            else:
                if not sch.sources:
                    sch.sources = [sch.source_url]
                schemas.setdefault(name, sch)                      # first spec wins on name clashes
    return DocsSnapshot(ops, VENDORS[vendor]["urls"][0], retrieved_at, from_cache, vendor=vendor, schemas=schemas)


async def get_snapshot(vendor: str, force_refresh: bool = False) -> tuple[DocsSnapshot | None, str | None]:
    if vendor not in VENDORS:
        return None, f"unknown vendor '{vendor}'"
    if vendor == "ramp":
        snap, err = await ramp_docs.get_snapshot(force_refresh=force_refresh)
        if snap:
            snap.vendor = "ramp"
        return snap, err

    cached = _read_cache(vendor)
    if cached and not force_refresh and time.time() - cached[1].get("epoch", 0) < ramp_docs.CACHE_TTL_S:
        return _snapshot_from(vendor, cached[0], cached[1]["retrieved_at"], True), None
    failed = _refresh_failed_at.get(vendor)
    if cached and not force_refresh and failed and time.time() - failed < ramp_docs.REFRESH_BACKOFF_S:
        return (_snapshot_from(vendor, cached[0], cached[1]["retrieved_at"], True),
                "refresh skipped after a recent failure; serving cached docs")
    try:
        texts = [await _download(u, ramp_docs.CACHED_TIMEOUT_S if cached else None) for u in VENDORS[vendor]["urls"]]
        snap = _snapshot_from(vendor, texts, ramp_docs._now(), False)
        if not snap.operations:
            raise ValueError("vendor specs parsed to zero operations; format may have changed")
        _write_cache(vendor, texts, snap.retrieved_at)
        _refresh_failed_at.pop(vendor, None)
        return snap, None
    except Exception as e:  # network, HTTP, size, YAML
        _refresh_failed_at[vendor] = time.time()
        err = f"{type(e).__name__}: {e}"[:300]
        if cached:
            return _snapshot_from(vendor, cached[0], cached[1]["retrieved_at"], True), err
        return None, err
