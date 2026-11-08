"""Simulated Brex seller profile. Technical answers come only from Brex's official docs:
public OpenAPI specs (transactions, expenses, webhooks) and developer guides."""

from seller.vendors.common import Facet, Lookup, VendorProfile

PRIMARY = "/v2/transactions/card/primary"
STABLE_ID = r"(?i)\bid\b[^.]{0,80}(does not change|never changes|remains (the same|stable|unchanged)|is stable)"

BREX = VendorProfile(
    vendor_id="brex",
    display_name="Brex",
    persona=("(fictional) Hi, I'm a simulated Brex solutions engineer built by the hackathon team. "
             "Everything technical I say is cited from Brex's public OpenAPI specs and developer guides."),
    lookups={"Q1": Lookup("list_operations", PRIMARY)},
    facets={
        "Q1": (Facet("list endpoint for card transactions", "native_capability", "op", PRIMARY),
               Facet("the list returns settled transactions only", "limitation", "search", "settled",
                     within=PRIMARY, core=False)),
        "Q2": (Facet("transaction id field", "api_primitive", "schema", "CardTransaction", pick=("id",)),
               Facet("id documented as stable across the transaction lifecycle", "guarantee", "search", "id",
                     within="/v2/transactions", require=STABLE_ID, alt=("unique", "posted", "duplicate"))),
        "Q3": (Facet("Money: integer amount in the smallest currency unit, ISO 4217 currency", "native_capability",
                     "schema", "Money", pick=("amount", "currency")),),
        "Q4": (Facet("cursor pagination (cursor / next_cursor)", "api_primitive", "field", PRIMARY,
                     pick=("cursor", "limit")),
               Facet("date filter on the transactions list (posted_at_start)", "api_primitive", "field", PRIMARY,
                     pick=("posted_at_start",)),
               Facet("filter for records changed since the last sync (updated_at_start)", "native_capability",
                     "field", PRIMARY, pick=("updated_at_start",), alt=("updated_at_start", "updated")),
               Facet("guidance for late-arriving or modified records", "integration_pattern", "search", "lookback",
                     within="/v2/transactions", core=False)),
        "Q5": (Facet("transaction id field", "api_primitive", "schema", "CardTransaction", pick=("id",)),
               Facet("id documented as stable across the transaction lifecycle", "guarantee", "search", "id",
                     within="/v2/transactions", require=STABLE_ID, alt=("unique", "posted", "duplicate")),
               Facet("vendor guidance on de-duplicating transaction reads", "integration_pattern", "search",
                     "de-duplicate", within="/v2/transactions", core=False)),
        "Q6": (Facet("event when a card transaction settles (clears)", "native_capability", "guide", "settled",
                     require=r"(?i)\b(settled|settles|posted|cleared|clears)\b",
                     require_title=r"(?i)webhook|x-webhooks|EventType", alt=("posted", "TRANSACTION", "card transaction")),
               Facet("card expense payment status event (EXPENSE_PAYMENT_UPDATED)", "native_capability", "guide",
                     "EXPENSE_PAYMENT_UPDATED", core=False),
               Facet("duplicate deliveries: same Webhook-Id when resent", "delivery_semantics", "guide",
                     "Webhook-Id resent", core=False)),
        "Q7": (Facet("read-only transaction scope", "scope", "guide", "transactions.card.readonly"),
               Facet("expenses: read-only vs manage scopes are separate", "scope", "guide", "expenses.card.readonly",
                     core=False)),
    },
)
