"""Evidence contract between the Crafting runner and Judas: judas.crafting_evaluation/v1.

Top level (all required):
  schema, vendor, scenario, requirement, sandbox, org, template, test_run_id, environment ("crafting"),
  execution_status (completed | partial | blocked | error), started_at, finished_at, tests[], summary,
  provenance, execution_log[]
  optional: error, service_health, vendor_api, buyer_sync_log, judas_independent_readback

Each test (all required):
  name, level (1 vendor operation | 2 vendor persistence | 3 buyer-application integration),
  operation, responsibility (vendor | buyer_connector | both), verification_method, input,
  started_at, duration_ms, vendor_resource_id, readback_verified, expected, observed,
  status (passed | failed | blocked), error

"blocked" always means a prerequisite (permission, plan limit, missing credential, earlier step) stopped
the test. It is never evidence that a vendor lacks a capability.
"""

from __future__ import annotations

SCHEMA = "judas.crafting_evaluation/v1"
TOP = ("schema", "vendor", "scenario", "requirement", "sandbox", "org", "template", "test_run_id", "environment",
       "execution_status", "started_at", "finished_at", "tests", "summary", "provenance", "execution_log")
TEST = ("name", "level", "operation", "responsibility", "verification_method", "input", "started_at", "duration_ms",
        "vendor_resource_id", "readback_verified", "expected", "observed", "status", "error")
STATUSES = {"completed", "partial", "blocked", "error"}
TEST_STATUSES = {"passed", "failed", "blocked"}


def summarize(tests: list[dict]) -> dict:
    s = {"total": len(tests), "passed": 0, "failed": 0, "blocked": 0}
    for t in tests:
        if t.get("status") in TEST_STATUSES:
            s[t["status"]] += 1
    s["by_level"] = {str(lv): {k: sum(1 for t in tests if t.get("level") == lv and t.get("status") == k)
                               for k in ("passed", "failed", "blocked")} for lv in (1, 2, 3)}
    return s


def validate(ev: dict) -> list[str]:
    p = [f"missing top-level field: {k}" for k in TOP if k not in ev]
    if ev.get("schema") != SCHEMA:
        p.append(f"schema must be {SCHEMA}")
    if ev.get("environment") != "crafting":
        p.append("environment must be 'crafting'")
    if ev.get("execution_status") not in STATUSES:
        p.append(f"bad execution_status: {ev.get('execution_status')}")
    for i, t in enumerate(ev.get("tests") or []):
        p += [f"test {i} ({t.get('name')}): missing {k}" for k in TEST if k not in t]
        if t.get("status") not in TEST_STATUSES:
            p.append(f"test {i}: bad status {t.get('status')}")
        if t.get("level") not in (1, 2, 3):
            p.append(f"test {i}: bad level {t.get('level')}")
    if ev.get("execution_status") == "completed" and not ev.get("tests"):
        p.append("completed run has no tests")
    return p
