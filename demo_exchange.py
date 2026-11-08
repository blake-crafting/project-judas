"""CLI demo: one real buyer -> seller A2A exchange. Start the seller first:
    .venv/bin/python -m seller.server
then:
    .venv/bin/python demo_exchange.py "Does Ramp expose API operations for expense and vendor records?"
"""
import asyncio
import json
import sys

from buyer.client import ask_seller, new_requirement, search_registry

DEFAULT_Q = "Does Ramp expose API operations for expense and vendor records?"


def print_summary(r: dict) -> None:
    print(f"status: {r['status']} | evidence_mode: {r['evidence_mode']}")
    for c in r["claims"]:
        print(f"  [{c['finding']}] {c['requirement']}  source={c['source_type']} verified={c['verified']}")
        print(f"      {c['detail']}")
        for e in c.get("evidence", [])[:3]:
            print(f"      - {e['title']:36} {e['path']:42} required={e['required_request_fields']}")
    print("unanswered:", r["unanswered"])
    print("limitations:")
    for line in r["limitations"]:
        print("  -", line)


async def main(question: str, requirements: list[str], summary: bool = False) -> None:
    print(f"BUYER QUESTION: {question}\n")
    hits = search_registry(question)
    print("1) REGISTRY SEARCH (local curated list):")
    for h in hits:
        print(f"   - {h['name']}  score={h['score']} matched={h['matched']} demo={h['is_demo']}")
    live = [h for h in hits if h.get("base_url")]
    if not live:
        print("   No seller with a running endpoint matched."); return
    target = live[0]
    print(f"\n2) CONTACTING: {target['name']} at {target['base_url']}\n")
    result = await ask_seller(target["id"], new_requirement(question, requirements))
    print("3) PROTOCOL TRACE:")
    for s in result["trace"]:
        print("   " + json.dumps(s))
    print("\n4) SELLER RESPONSE (structured A2A artifact):")
    if summary:
        print_summary(result["response"])
    else:
        print(json.dumps(result["response"], indent=2))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--summary"]
    q = args[0] if args else DEFAULT_Q
    reqs = args[1].split(",") if len(args) > 1 else []
    asyncio.run(main(q, [r.strip() for r in reqs if r.strip()], summary="--summary" in sys.argv))
