"""Route typed Judas messages (A2A data parts) to the right seller handler.

Messages without a "schema" are legacy Workflow-1 requests and go to `legacy`.
"""

from __future__ import annotations

from judas.contracts import BUYER_REQUIREMENTS, DEMO_REQUEST, FOLLOW_UP, SCHEMAS, error_payload, validate
from seller.vendors.common import VendorProfile, build_follow_up, build_vendor_response


async def dispatch(payload: dict, profile: VendorProfile, legacy=None) -> dict:
    schema = payload.get("schema") if isinstance(payload, dict) else None
    eid = payload.get("evaluation_id") if isinstance(payload, dict) else None
    if schema is None:
        if legacy is None:
            return error_payload(eid, "missing schema")
        return await legacy(payload)
    if schema not in SCHEMAS:
        return error_payload(eid, f"unsupported schema '{str(schema)[:80]}'")
    if schema not in (BUYER_REQUIREMENTS, DEMO_REQUEST, FOLLOW_UP):
        return error_payload(eid, f"this seller does not accept '{schema}' messages")
    problems = validate(payload)
    if problems:
        return error_payload(eid, f"invalid {schema}: " + "; ".join(problems))
    if schema == DEMO_REQUEST:
        from seller.vendors.demo import run_demo
        try:
            return await run_demo(payload, profile.vendor_id, profile.display_name)
        except BaseException as e:            # sandbox down etc.: say so, never pretend
            while isinstance(e, BaseExceptionGroup) and e.exceptions:
                e = e.exceptions[0]
            return error_payload(eid, f"demo could not run: {type(e).__name__}: {str(e)[:200]}")
    if schema == FOLLOW_UP:
        try:
            return await build_follow_up(payload, profile)
        except BaseException as e:            # docs MCP down etc.
            while isinstance(e, BaseExceptionGroup) and e.exceptions:
                e = e.exceptions[0]
            return error_payload(eid, f"follow-up could not run: {type(e).__name__}: {str(e)[:200]}")
    return await build_vendor_response(payload, profile)
