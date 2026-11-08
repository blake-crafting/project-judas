import asyncio
from pathlib import Path

import pytest

from seller import ramp_docs, vendor_docs

FIX = Path(__file__).parent / "fixtures"


def _brex():
    return vendor_docs.parse_openapi((FIX / "brex_transactions_excerpt.yaml").read_text(), "u")


def test_openapi_operation_params_and_response_fields():
    ops, _ = _brex()
    op = next(o for o in ops if o.path == "/v2/transactions/card/primary")
    assert op.method == "GET" and op.title == "List primary card transactions"
    assert {"cursor", "limit", "posted_at_start"} <= set(op.query_params)          # $ref param resolved
    assert "De-duplicate the overlapping results by transaction" in op.description  # param descriptions folded in
    assert any(f.startswith("id") for f in op.response_fields)                     # Page_ → items → CardTransaction
    assert op.source_url == "u"


def test_openapi_schemas_money_allof_and_scopes():
    _, schemas = _brex()
    assert schemas["Money"].fields["amount"].startswith("integer (int64)")
    assert "merchant" in schemas["CardTransaction"].fields                         # allOf resolved, no crash
    assert "transactions.card.readonly" in schemas["SecurityScopes"].fields


def test_openapi_tolerates_missing_summary_and_description():
    ops, _ = vendor_docs.parse_openapi("openapi: 3.0.1\npaths:\n  /x:\n    get: {}\n", "u")
    assert ops[0].title == "GET /x" and ops[0].description == ""


def test_ramp_response_fields_parsed():
    ops = ramp_docs.parse((FIX / "ramp_llms_api_excerpt.txt").read_text())
    lt = next(o for o in ops if o.title == "List transactions")
    names = {f.split(":")[0].rstrip("?") for f in lt.response_fields}
    assert {"id", "amount", "currency_code", "minor_unit_conversion_rate", "next"} <= names
    assert lt.source_url == ramp_docs.SOURCE_URL


def test_unknown_vendor_rejected():
    snap, err = asyncio.run(vendor_docs.get_snapshot("acme"))
    assert snap is None and "unknown vendor" in err


def test_brex_snapshot_from_seeded_cache(seeded_cache):
    snap, err = asyncio.run(vendor_docs.get_snapshot("brex"))
    assert err is None and snap.vendor == "brex" and "Money" in snap.schemas
    assert any(o.path == "/v2/transactions/card/primary" for o in snap.operations)


@pytest.mark.live
def test_live_brex_specs_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(ramp_docs, "CACHE_DIR", tmp_path)
    snap, err = asyncio.run(vendor_docs.get_snapshot("brex", force_refresh=True))
    assert snap is not None and err is None and snap.from_cache is False
    assert any(o.path == "/v2/transactions/card/primary" for o in snap.operations)


EXPENSES_WITH_SCOPES = """openapi: 3.0.1
paths:
  /v1/expenses:
    get:
      summary: "\\n  List expenses\\n  "
components:
  securitySchemes:
    OAuth2:
      type: oauth2
      flows:
        authorizationCode:
          scopes:
            expenses.card.readonly: View card expenses data
"""


def test_security_scopes_merged_across_specs_with_sources():
    snap = vendor_docs._snapshot_from("brex", [(FIX / "brex_transactions_excerpt.yaml").read_text(),
                                               EXPENSES_WITH_SCOPES], "t", True)
    scopes = snap.schemas["SecurityScopes"]
    assert {"transactions.card.readonly", "expenses.card.readonly"} <= set(scopes.fields)   # not overwritten
    assert snap.schemas["Money"].source_url == vendor_docs.BREX_TX                          # per-schema source


def test_titles_whitespace_normalized():
    ops, _ = vendor_docs.parse_openapi(EXPENSES_WITH_SCOPES, "u")
    assert ops[0].title == "List expenses"


def test_stale_cache_served_without_retrying_download_after_a_failure(seeded_cache, monkeypatch):
    import json as _json, time as _time
    meta = seeded_cache / "brex.meta.json"
    m = _json.loads(meta.read_text()); m["epoch"] = _time.time() - 10 * 3600; meta.write_text(_json.dumps(m))  # stale
    calls = {"n": 0}

    async def boom(url, timeout=None):
        calls["n"] += 1
        raise ConnectionError("blackhole")
    monkeypatch.setattr(vendor_docs, "_download", boom)
    vendor_docs._refresh_failed_at.clear()
    s1, e1 = asyncio.run(vendor_docs.get_snapshot("brex"))
    s2, e2 = asyncio.run(vendor_docs.get_snapshot("brex"))
    assert s1 and s1.from_cache and e1 and s2 and s2.from_cache
    assert calls["n"] == 1                      # second call did not retry inside the backoff window


def test_merged_scopes_cite_the_primary_spec_without_broken_links():
    snap = vendor_docs._snapshot_from("brex", [(FIX / "brex_transactions_excerpt.yaml").read_text(),
                                               EXPENSES_WITH_SCOPES], "t", True)
    sc = snap.schemas["SecurityScopes"]
    assert sc.source_url == vendor_docs.BREX_TX and " + " not in sc.source_url
    assert set(sc.sources) == {vendor_docs.BREX_TX, vendor_docs.BREX_EX}
