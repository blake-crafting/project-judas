"""Read-only feasibility probe: connect to each vendor's official MCP, list the real tools,
save the catalog to qa/vendor-probes/. Prints status lines only (never credentials)."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vendors_live.config import status  # noqa: E402
from vendors_live.mcp_live import ENDPOINTS, vendor_session, tool_record  # noqa: E402

OUT = ROOT / "qa" / "vendor-probes"


async def probe(vendor: str) -> None:
    try:
        async with vendor_session(vendor) as client:
            tools = [tool_record(t) for t in (await client.list_tools()).tools]
    except BaseException as e:
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        print(f"{vendor}: FAILED to list tools ({type(e).__name__}: {str(e)[:160]})")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"{vendor}-tools-{stamp}.json"
    path.write_text(json.dumps({"vendor": vendor, "endpoint": ENDPOINTS[vendor], "listed_at": stamp,
                                "tool_count": len(tools), "tools": tools}, indent=1))
    ro = sum(t["read_only"] for t in tools)
    print(f"{vendor}: {len(tools)} tools ({ro} marked read-only) -> {path.relative_to(ROOT)}")
    print("   " + ", ".join(t["name"] for t in tools))


async def main() -> None:
    st = status()
    for vendor, ok in st.items():
        if not ok:
            print(f"{vendor}: not configured (skipped)")
            continue
        await probe(vendor)


asyncio.run(main())
