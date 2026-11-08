"""Simulated Ramp seller profile. Technical answers come only from Ramp's official docs:
the API reference (llms-api.txt), the canonical OpenAPI spec (transaction parts) and guides."""

from seller.vendors.common import Facet, Lookup, VendorProfile

TX = "/developer/v1/transactions"
STABLE_ID = r"(?i)\bid\b[^.]{0,80}(does not change|never changes|remains (the same|stable|unchanged)|is stable)"

RAMP = VendorProfile(
    vendor_id="ramp",
    display_name="Ramp",
    persona=("(fictional) Hi, I'm a simulated Ramp solutions engineer built by the hackathon team. "
             "Everything technical I say is cited from Ramp's public API reference and developer guides."),
    lookups={"Q1": Lookup("list_operations", TX)},
    facets={
        "Q1": (Facet("list endpoint for card transactions", "native_capability", "op", TX),),
        "Q2": (Facet("transaction id field", "api_primitive", "field", TX, pick=("id",)),
               Facet("id documented as stable across the transaction lifecycle", "guarantee", "guide", "id",
                     require=STABLE_ID, require_title=r"(?i)transaction",
                     alt=("transaction id", "pending cleared", "original_transaction_id", "unique")),
               Facet("refunds point to the original transaction id", "api_primitive", "field", TX,
                     pick=("original_transaction_id",), core=False)),
        "Q3": (Facet("money rule: integers in the smallest currency unit", "native_capability", "guide",
                     "smallest denomination", require=r"(?i)smallest denomination", require_title=r"(?i)monetary"),
               Facet("entity_amount: settled amount in the smallest unit, with currency", "api_primitive", "guide",
                     "smallest denomination", require_title=r"Transaction/properties/entity_amount$"),
               Facet("transaction `amount` / `currency_code` are deprecated (use entity_amount)", "deprecation",
                     "guide", "deprecated", require_title=r"Transaction/properties/amount$", core=False)),
        "Q4": (Facet("cursor pagination (page.next / start)", "api_primitive", "guide", "next start cursor"),
               Facet("date filter on the transactions list (from_date)", "api_primitive", "guide",
                     "query parameter from_date", require_title=r"/developer/v1/transactions \?from_date"),
               Facet("filter for records changed since the last sync (updated_after)", "native_capability", "guide",
                     "query parameter updated_after", require_title=r"/developer/v1/transactions \?updated_after",
                     alt=("updated_after", "updated_at", "synced_after")),
               Facet("guidance for late-arriving or modified records", "integration_pattern", "guide",
                     "lookback", core=False)),
        "Q5": (Facet("transaction id field", "api_primitive", "field", TX, pick=("id",)),
               Facet("id documented as stable across the transaction lifecycle", "guarantee", "guide", "id",
                     require=STABLE_ID, require_title=r"(?i)transaction",
                     alt=("transaction id", "pending cleared", "original_transaction_id", "unique")),
               Facet("vendor guidance on de-duplicating transaction reads", "integration_pattern", "guide",
                     "duplicate", require=r"(?i)transaction[^.]{0,120}de-?duplicat|de-?duplicat[^.]{0,120}transaction",
                     core=False)),
        "Q6": (Facet("event when a card transaction settles (clears)", "native_capability", "guide", "transactions.cleared"),
               Facet("event when a card transaction is authorized", "native_capability", "guide",
                     "transactions.authorized", core=False),
               Facet("duplicate deliveries: same event id on retries", "delivery_semantics", "guide",
                     "same event id retry", core=False)),
        "Q7": (Facet("read-only transaction scope", "scope", "guide", "transactions:read"),
               Facet("separate scope needed to modify transactions", "scope", "guide", "transactions:write",
                     core=False)),
    },
)
