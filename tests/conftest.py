"""Shared test helpers: seed the vendor-docs cache from offline fixtures so tests
never depend on docs.ramp.com / developer.brex.com being reachable."""

import json
import shutil
import time
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
EMPTY_OPENAPI = "openapi: 3.0.1\ninfo: {title: Expenses API, version: 1.0.0}\npaths: {}\n"


def seed_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    stamp = "2026-11-07T00:00:00+00:00"
    shutil.copy(FIX / "ramp_llms_api_excerpt.txt", cache_dir / "llms-api.txt")
    (cache_dir / "llms-api.meta.json").write_text(json.dumps(
        {"retrieved_at": stamp, "epoch": now, "source_url": "https://docs.ramp.com/llms-api.txt"}))
    shutil.copy(FIX / "brex_transactions_excerpt.yaml", cache_dir / "brex-0.txt")
    (cache_dir / "brex-1.txt").write_text(EMPTY_OPENAPI)
    (cache_dir / "brex.meta.json").write_text(json.dumps({
        "retrieved_at": stamp, "epoch": now,
        "urls": ["https://developer.brex.com/_spec/openapi/transactions_api.yaml",
                 "https://developer.brex.com/_spec/openapi/expenses_api.yaml"]}))
    seed_guides(cache_dir)


GUIDE_FIXTURES = {
    "ramp": [
        "# Monetary Values\nMost Ramp API monetary values use integers in the smallest denomination of the relevant currency.\n",
        "# Permission model and scopes\nScopes follow the pattern resource:permission, such as `transactions:read`. "
        "`transactions:write` updates transaction data.\n",
        "# 2. Fetch subsequent pages\nUse the complete URL in the next field. The URL includes the start cursor.\n",
        "# Available events\n`transactions.authorized` Transaction has been authorized. `transactions.cleared` cleared.\n"
        "# Retry schedule\nThe same event id is used for all retry attempts.\n",
        '{"paths": {"/developer/v1/transactions": {"get": {"parameters": [{"name": "from_date", "description": '
        '"Filter for transactions after the given date."}]}}}, "components": {"schemas": {"Transaction": {"properties": '
        '{"entity_amount": {"description": "The value is in the smallest denomination of the currency."}, '
        '"amount": {"description": "Deprecated. Use entity_amount."}}}}}}',
    ],
    "brex": [
        "openapi: 3.0.1\ninfo: {title: Webhooks API, version: 1.0.0}\ncomponents:\n  schemas:\n    WebhookEventType:\n"
        "      type: string\n      enum: [EXPENSE_PAYMENT_UPDATED, USER_UPDATED]\n",
        "# Pagination\nThe response will contain next_cursor.\n",
        "# Transactions API\n`transactions.card.readonly` View card transaction data. "
        "`expenses.card.readonly` View card expense data. `expenses.card` manage.\n",
        "# Idempotency\nIdempotency keys on GET have no effect.\n",
        "# Verify signature\nThe Webhook-Id will be the same when the same webhook is being resent.\n",
    ],
}


def seed_guides(cache_dir: Path) -> None:
    from seller.vendor_guides import GUIDES
    for vendor, urls in GUIDES.items():
        texts = GUIDE_FIXTURES[vendor]
        assert len(texts) == len(urls), "update GUIDE_FIXTURES when GUIDES changes"
        for i, t in enumerate(texts):
            (cache_dir / f"guides-{vendor}-{i}.txt").write_text(t)
        (cache_dir / f"guides-{vendor}.meta.json").write_text(json.dumps(
            {"retrieved_at": "2026-11-07T00:00:00+00:00", "epoch": time.time(), "urls": urls}))


@pytest.fixture
def seeded_cache(tmp_path, monkeypatch):
    from seller import ramp_docs
    cache = tmp_path / "cache"
    seed_cache(cache)
    monkeypatch.setenv("JUDAS_CACHE_DIR", str(cache))
    monkeypatch.setattr(ramp_docs, "CACHE_DIR", cache)
    return cache
