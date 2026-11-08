"""Live, real-vendor evaluation: Airtable vs monday.com (project tracking).

Trust boundary: the seller adapters (ours, over A2A) perform operations through each vendor's
OFFICIAL hosted MCP server and report real object ids. The buyer never trusts that report alone:
it re-reads every object through the vendor's separate public API and checks the values.

Evidence per acceptance test: verified (read back and matched) > executed (seller reported it,
readback missing or failed) > tool_available > blocked (prerequisite missing) / failed.
"""

from __future__ import annotations

VENDORS = {
    "airtable": {"name": "Airtable", "agent_id": "airtable-live-adapter",
                 "mcp": "https://mcp.airtable.com/mcp", "readback": "Airtable REST API (api.airtable.com/v0)",
                 "native_a2a": None},
    "monday": {"name": "monday.com", "agent_id": "monday-live-adapter",
               "mcp": "https://mcp.monday.com/mcp", "readback": "monday GraphQL API (api.monday.com/v2)",
               "native_a2a": "https://api.monday.com/platform-ai-gateway/a2a/platform-agent/jsonrpc"},
}
SCENARIO = {
    "id": "incident_tracking_v1",
    "label": "Incident tracking: Airtable vs monday.com",
    "request": ("We operate a multi-service e-commerce application. Evaluate Airtable and monday.com for operational "
                "incident tracking. Demonstrate how each handles an actual application-generated incident, repeated "
                "events, and status synchronization."),
    "tasks": [{"name": "checkout-service: payment API 5xx spike", "status": "Open", "priority": "P1"},
              {"name": "inventory-service: stock sync lag", "status": "Investigating", "priority": "P2"},
              {"name": "search-service: slow queries", "status": "Open", "priority": "P3"}],
    "update": {"name": "checkout-service: payment API 5xx spike", "status": "Investigating"},
}
STATUS_CHOICES = ("Open", "Investigating", "Resolved")
PRIORITY_CHOICES = ("P1", "P2", "P3")
TESTS = [
    {"id": "A1", "requirement": "Incident log per run", "test": "Create an incident log container for this run",
     "success": "The container exists and holds the run's incidents", "must": True},
    {"id": "A2", "requirement": "Record incidents with status and severity", "test": "Create 3 incidents",
     "success": "All 3 incidents read back with the requested status and severity", "must": True},
    {"id": "A3", "requirement": "Update incident status", "test": "Move the checkout incident to Investigating",
     "success": "The new status persists on readback", "must": True},
    {"id": "A4", "requirement": "Programmatic access for agents", "test": "List the vendor MCP tool catalog",
     "success": "Create, update and read tools are available to this account", "must": True},
]
# Stage 3: buyer-environment compatibility, run by the Crafting integration test runner.
CRAFTING_TESTS = [
    {"id": "C1", "runner_id": "incident_ingest", "requirement": "Application-generated incident",
     "test": "A service in our Crafting sandbox raises a real incident into the vendor"},
    {"id": "C2", "runner_id": "repeated_events", "requirement": "Repeated events",
     "test": "The same incident fires 3 times; it must stay one incident"},
    {"id": "C3", "runner_id": "status_sync", "requirement": "Status synchronization",
     "test": "The service recovers; the vendor record must sync to Resolved"},
]
UNTESTED = ["Assigning on-call owners (needs more than one user)", "Permissions and read-only roles",
            "Vendor-side webhooks and automations", "Load and latency at production volume"]
LEVEL_RANK = {"not_run": 1, "verified": 5, "executed": 4, "tool_available": 3, "unknown": 2, "blocked": 1, "failed": 0}
CAVEAT = ("Live evidence from one run in free-plan test workspaces with fictional data. Vendor capability means "
          "the objects were created through the vendor's official MCP server and read back through its separate "
          "API. Buyer-environment compatibility comes only from the Crafting sandbox run. Neither proves "
          "production reliability, performance, or anything not tested here.")


def crafting_evidence(stage: dict | None) -> dict[str, dict]:
    """Stage 3 evidence per Crafting test: the runner's own result, upgraded to verified only when Judas
    independently re-read the resources it reported."""
    out = {}
    stage = stage or {}
    status = stage.get("status")
    if status != "ran":
        for t in CRAFTING_TESTS:
            out[t["id"]] = {"level": "not_run" if status in (None, "not_run") else "unknown",
                            "detail": stage.get("reason") or stage.get("error") or "Crafting stage not run"}
        return out
    res = stage.get("result") or {}
    by = {x.get("id"): x for x in res.get("tests") or [] if isinstance(x, dict)}
    rb = stage.get("judas_readback") or {}
    for t in CRAFTING_TESTS:
        r = by.get(t["runner_id"])
        if not r:
            out[t["id"]] = {"level": "unknown", "detail": "runner did not report this test"}
        elif r.get("status") == "pass":
            ok = rb.get("found") and (t["id"] != "C3" or rb.get("status_ok"))
            out[t["id"]] = {"level": "verified" if ok else "executed",
                            "detail": (r.get("detail") or "") + ("" if ok else " (Judas could not confirm independently)")}
        elif r.get("status") == "skipped":
            out[t["id"]] = {"level": "not_run", "detail": r.get("detail") or "skipped by the Crafting runner"}
        elif r.get("status") == "fail":
            out[t["id"]] = {"level": "failed", "detail": r.get("detail") or "failed"}
        else:
            out[t["id"]] = {"level": "unknown", "detail": r.get("detail") or r.get("status") or "error"}
    return out


def compat_verdict(cev: dict[str, dict[str, dict]]) -> tuple[str, str]:
    names = {v: VENDORS[v]["name"] for v in VENDORS}
    ok = {v: sum(e["level"] == "verified" for e in cev[v].values()) for v in VENDORS}
    ran = {v: any(e["level"] in ("verified", "executed", "failed") for e in cev[v].values()) for v in VENDORS}
    if not any(ran.values()):
        return "not_run", "The Crafting integration stage did not run; buyer-environment compatibility is untested."
    n = len(CRAFTING_TESTS)
    parts = [f"{names[v]} {ok[v]}/{n}" for v in VENDORS]
    if not all(ran.values()):
        missing = [names[v] for v in VENDORS if not ran[v]]
        return "compat_incomplete", (f"Crafting sandbox: {', '.join(parts)} verified; {', '.join(missing)} not run in the "
                                     "sandbox yet, so compatibility can't be compared.")
    if all(ok[v] == n for v in VENDORS):
        return "both_compatible", f"Both passed every buyer-environment test in the Crafting sandbox ({', '.join(parts)})."
    best = max(VENDORS, key=lambda v: ok[v])
    if ok[best] > min(ok.values()):
        return f"compat_favor:{best}", f"In the Crafting sandbox: {', '.join(parts)} verified."
    return "compat_incomplete", f"Crafting sandbox results incomplete: {', '.join(parts)} verified."


def expected_state() -> dict[str, dict]:
    up = SCENARIO["update"]
    return {t["name"]: {"Status": up["status"] if t["name"] == up["name"] else t["status"], "Priority": t["priority"]}
            for t in SCENARIO["tasks"]}


def check_readback(actual: dict[str, dict], container_ok: bool) -> list[dict]:
    """Compare what the buyer read back with what it asked for."""
    checks = [{"test": "A1", "item": "(container)", "field": "contains this run's tasks", "expected": True,
               "actual": container_ok, "pass": bool(container_ok)}]
    for name, fields in expected_state().items():
        for f, v in fields.items():
            got = (actual.get(name) or {}).get(f)
            test = "A3" if (name == SCENARIO["update"]["name"] and f == "Status") else "A2"
            checks.append({"test": test, "item": name, "field": f, "expected": v, "actual": got, "pass": got == v})
    return checks


def evidence(result: dict | None, error: str | None, checks: list[dict], needed_tools: set[str]) -> dict[str, dict]:
    """Evidence level per acceptance test for one vendor."""
    out = {}
    tools = set((result or {}).get("tools_available") or [])
    ran = {s.get("test") for s in (result or {}).get("steps") or [] if s.get("ok")}
    for t in TESTS:
        tid = t["id"]
        if tid == "A4":
            have = needed_tools <= tools
            lvl = "tool_available" if have else ("failed" if tools else
                  ("blocked" if error and ("blocked" in error.lower() or "not configured" in error.lower()) else "unknown"))
            out[tid] = {"level": lvl,
                        "detail": f"{len(tools)} tools listed; needed: {', '.join(sorted(needed_tools))}"
                        if tools else (error or "no tool catalog")}
            continue
        mine = [c for c in checks if c["test"] == tid]
        if error and not result:
            # A missing prerequisite is "blocked"; any other error leaves the requirement unestablished.
            out[tid] = {"level": "blocked" if "blocked" in error.lower() or "not configured" in error.lower()
                        else "unknown", "detail": error}
        elif mine and all(c["pass"] for c in mine):
            out[tid] = {"level": "verified", "detail": f"{len(mine)}/{len(mine)} readback checks passed"}
        elif tid in ran and not mine:
            out[tid] = {"level": "executed", "detail": "seller reported success; no independent readback"}
        elif mine:
            bad = [c for c in mine if not c["pass"]]
            out[tid] = {"level": "failed", "detail": f"{len(bad)} readback check(s) did not match"}
        else:
            out[tid] = {"level": "unknown", "detail": "not executed"}
    return out


def verdict(ev: dict[str, dict[str, dict]]) -> tuple[str, str]:
    names = {v: VENDORS[v]["name"] for v in VENDORS}
    musts = [t["id"] for t in TESTS if t["must"]]
    proven = {v: [m for m in musts if ev[v][m]["level"] in ("verified", "tool_available")] for v in VENDORS}
    none = [v for v in VENDORS if not any(ev[v][m]["level"] in ("verified", "executed", "tool_available", "failed")
                                         for m in musts)]
    if none:
        return "insufficient_evidence", (f"No live evidence from {', '.join(names[v] for v in none)} in this run "
                                         "(blocked or the operation did not complete); not a product verdict.")
    full = [v for v in VENDORS if len(proven[v]) == len(musts)]
    if len(full) == 2:
        return ("both_proven", "Both vendors proved every tested requirement in their real products; the tested "
                "behavior shows no difference. Decide on the requirements that are still untested.")
    if len(full) == 1:
        v = full[0]
        other = next(x for x in VENDORS if x != v)
        miss = [m for m in musts if m not in proven[other]]
        return f"live_favor:{v}", f"Only {names[v]} proved every tested requirement; {names[other]} did not prove {', '.join(miss)}."
    return "additional_testing_required", "Neither vendor proved every tested requirement in this run."
