"""Two FICTIONAL seller agents, served as genuine A2A endpoints in one process.

  http://127.0.0.1:9103/ledgerleaf   LedgerLeaf AP Agent (fictional)
  http://127.0.0.1:9103/payrollpal   PayrollPal HR Agent (fictional)

They answer from seller/demo_profiles.json (invented data, re-read on every
request so a profile edit changes the next answer). Their claims are always
source_type="demo_profile", verified=False. The protocol exchange is real;
the vendor facts are not.

Run:  .venv/bin/python -m seller.demo_sellers
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import uvicorn
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette
from starlette.routing import Mount

from seller import knowledge
from seller.server import build_routes

HOST = os.environ.get("DEMO_SELLERS_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEMO_SELLERS_PORT", "9103"))
PUBLIC_BASE = os.environ.get("DEMO_SELLERS_PUBLIC_URL", f"http://{HOST}:{PORT}")
PROFILES_PATH = Path(os.environ.get("DEMO_PROFILES_PATH", Path(__file__).with_name("demo_profiles.json")))


def load_profiles() -> dict:
    return {k: v for k, v in json.loads(PROFILES_PATH.read_text()).items() if not k.startswith("_")}


def _norm(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()
    return " " + " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words) + " "


def _match(text: str, keywords: list[str]) -> bool:
    t = _norm(text)
    return any(_norm(k) in t for k in keywords)


def answer_from_profile(vendor_id: str, requirement: dict) -> dict:
    profile = load_profiles()[vendor_id]
    question = str(requirement.get("question", ""))[:1000]
    requested = [str(r)[:100] for r in requirement.get("requirements", [])][:10]
    caps = profile["capabilities"]
    if requested:
        to_check = requested
    else:  # same topic detection as the Ramp adapter, plus this profile's own capabilities
        to_check = knowledge.requirements_to_check(requirement)
        for c in caps:
            if _match(question, c["keywords"]) and not any(_match(x, c["keywords"]) for x in to_check):
                to_check.append(c["capability"])

    claims = []
    for req in to_check:
        cap = next((c for c in caps if _match(req, c["keywords"]) or _norm(req) == _norm(c["capability"])), None)
        claims.append({
            "requirement": req,
            "finding": cap["finding"] if cap else "unknown",
            "detail": cap["detail"] if cap else "Not in this vendor's (invented) profile.",
            "source_type": "demo_profile" if cap else None,
            "source_url": None, "observed_via_tool": False, "verified": False, "evidence": [],
        })
    findings = {c["finding"] for c in claims}
    status = ("answered" if findings == {"documented"} else
              "partially_answered" if "documented" in findings else "unanswered")
    return {
        "request_id": str(requirement.get("request_id", ""))[:64],
        "vendor_id": f"{vendor_id}-demo",
        "status": status,
        "evidence_mode": "demo_profile",
        "claims": claims,
        "unanswered": [c["requirement"] for c in claims if c["finding"] == "unknown"] or ([] if claims else [question]),
        "limitations": ["FICTIONAL vendor: every claim comes from an invented demo profile, not a real company."],
        "tool_calls": [],
    }


def _card(vendor_id: str, profile: dict) -> AgentCard:
    return AgentCard(
        name=profile["name"],
        description=profile["description"],
        version="0.1.0",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC",
                                             url=f"{PUBLIC_BASE}/{vendor_id}/", protocol_version="1.0")],
        skills=[AgentSkill(id=f"answer_{vendor_id}_questions", name="Answer capability questions (fictional)",
                           description="Answers from an invented capability profile.",
                           tags=[c["capability"] for c in profile["capabilities"]] + ["demo"])],
    )


def build_app() -> Starlette:
    mounts = []
    for vendor_id, profile in load_profiles().items():
        async def responder(req: dict, _vid: str = vendor_id) -> dict:
            r = answer_from_profile(_vid, req)
            print(f"[demo-seller:{_vid}] status={r['status']} claims={[c['finding'] for c in r['claims']]}", flush=True)
            return r
        mounts.append(Mount(f"/{vendor_id}", routes=build_routes(_card(vendor_id, profile), responder)))
    return Starlette(routes=mounts)


if __name__ == "__main__":
    uvicorn.run(build_app(), host=HOST, port=PORT)
