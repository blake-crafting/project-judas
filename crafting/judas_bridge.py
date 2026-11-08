"""Judas <-> Crafting bridge (stdin/stdout contract for the Judas orchestration session).

  python crafting/judas_bridge.py --vendor airtable|monday
  stdin : judas.crafting_test_request/v1   stdout: ONE judas.crafting_test_result/v1 JSON   logs: stderr

Wraps crafting.runner.run_vendor_evaluation (the full evidence file is linked as evidence_path).
Exit code 0 whenever valid JSON was produced, even if tests failed or were blocked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JUDAS_CREDS_ROOT", str(Path.home() / "Hackathon" / "agent-discovery-lab"))

from crafting import runner  # noqa: E402

GROUPS = {  # Judas test id -> the acceptance steps (all must pass) that prove it
    "incident_ingest": ["generate_order_failure", "create_vendor_task", "readback_created_task"],
    "repeated_events": ["duplicate_event_no_duplicate_task"],
    "status_sync": ["update_internal_status", "propagate_status_to_vendor", "readback_updated_status", "final_state_consistent"],
}
EXTRA = ["recover_from_transient_network_failure", "recover_from_lost_response_without_duplicate"]
PLAIN = {
    "incident_ingest": "Checkout service failed a payment, emitted an incident event over HTTP; the incident service stored it in "
                       "Postgres and created the vendor record, which a GET by id read back field-for-field.",
    "repeated_events": "The same failure event was delivered 3 times; the vendor still has exactly one record for its Incident Key "
                       "and the internal incident counts 3 occurrences.",
    "status_sync": "Internal status moved to investigating, then resolved; each change was pushed to the vendor and read back "
                   "(final vendor Status = Resolved, internal and vendor state match for every incident).",
    "recover_from_transient_network_failure": "One injected connection failure before sending; the connector retried and created "
                                              "exactly one record.",
    "recover_from_lost_response_without_duplicate": "The vendor received a create but the response was dropped (injected); before "
                                                    "retrying, the connector looked the record up by Incident Key and reused it: no duplicate.",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def group_status(tests: dict, names: list[str]) -> tuple[str, str]:
    got = [tests.get(n) for n in names]
    if any(t is None for t in got):
        return "skipped", "not executed: " + ", ".join(n for n, t in zip(names, got) if t is None)
    bad = [t for t in got if t["status"] != "passed"]
    if not bad:
        return "pass", ""
    if all(t["status"] == "blocked" for t in bad):
        return "skipped", "; ".join(f"{t['name']}: {t.get('error')}" for t in bad)
    return "fail", "; ".join(f"{t['name']}: expected {json.dumps(t.get('expected'))[:120]} observed "
                             f"{json.dumps(t.get('observed'))[:160]} {t.get('error') or ''}" for t in bad)


def sandbox_identity(name: str) -> dict:
    ident = {"name": name, "id": None, "org": runner.ORG, "template": runner.TEMPLATE,
             "url": f"https://crafting.sandboxes.site/sandbox/{name}", "access": "private (owner only)"}
    try:
        d = json.loads(runner.cs("sandbox", "show", name, "-o", "json", timeout=60))
        ident["id"] = (d.get("meta") or {}).get("id") or d.get("id")
    except Exception as e:  # noqa: BLE001
        log(f"sandbox show failed: {e}")
    return ident


def readback_checks(ev: dict) -> list[dict]:
    rb = ev.get("judas_independent_readback") or {}
    return [{"field": "Status", "resource_id": r["vendor_resource_id"], "expected": r["expected_status"],
             "actual": (r.get("observed") or {}).get("status"), "pass": r["match"]} for r in rb.get("records", [])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", choices=["airtable", "monday"], required=True)
    a = ap.parse_args()
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    req = json.loads(raw) if raw.strip() else {}
    vendor = a.vendor
    errors: list[str] = []
    if req.get("vendor") and req["vendor"] != vendor:
        errors.append(f"request vendor {req['vendor']!r} != --vendor {vendor!r}; using --vendor")
    run_id = (req.get("evaluation_id") or "")[-40:] or None
    log(f"[crafting] running {vendor} evaluation in sandbox {runner.SANDBOXES[vendor]} (run {run_id})")
    ev = runner.run_vendor_evaluation(vendor, test_run_id=run_id)
    log(f"[crafting] {ev['execution_status']} {ev['summary']} evidence={ev['evidence_path']}")
    by = {t["name"]: t for t in ev["tests"]}
    wanted = req.get("tests") or list(GROUPS)
    tests = []
    for tid in wanted:
        if tid not in GROUPS:
            tests.append({"id": tid, "status": "skipped", "detail": "not implemented by the Crafting runner"})
            continue
        st, why = group_status(by, GROUPS[tid])
        if ev["execution_status"] in ("blocked", "error") and not by:
            st, why = "error", f"Crafting run did not execute tests: {ev.get('error')}"
        tests.append({"id": tid, "status": st, "detail": PLAIN[tid] if st == "pass" else why,
                      "steps": [{"name": n, "status": by[n]["status"]} for n in GROUPS[tid] if n in by]})
    extra = []
    for n in EXTRA:
        st, why = group_status(by, [n])
        extra.append({"id": n, "status": st, "detail": PLAIN[n] if st == "pass" else why})
    ids = []
    for t in ev["tests"]:
        rid = t.get("vendor_resource_id")
        if rid and rid not in ids:
            ids.append(rid)
    from crafting.creds import STATE_DIR
    cfg = json.loads((STATE_DIR / "containers.json").read_text())[vendor]
    roles = {by.get("create_vendor_task", {}).get("vendor_resource_id"): "status_sync_incident",
             by.get("recover_from_transient_network_failure", {}).get("vendor_resource_id"): "transient_retry_incident",
             by.get("recover_from_lost_response_without_duplicate", {}).get("vendor_resource_id"): "lost_response_incident"}
    expected = {r["resource_id"]: r["expected"] for r in readback_checks(ev)}
    resources = []
    for rid in ids:
        r = {"kind": "record" if vendor == "airtable" else "item", "id": rid, "role": roles.get(rid, "incident"),
             "expected_status": expected.get(rid),
             "url": (f"{cfg['url']}/{rid}" if vendor == "airtable" else f"{cfg['url']}/pulses/{rid}")}
        r.update({"base_id": cfg["base_id"], "table_id": cfg["table_id"]} if vendor == "airtable"
                 else {"board_id": cfg["board_id"], "group_id": cfg["group_id"]})
        resources.append(r)
    if ev.get("error"):
        errors.append(ev["error"])
    tail = [f"{s['at']} {'ok ' if s['ok'] else 'ERR'} {s['step']}: {s['detail']}" for s in ev["execution_log"]][-20:]
    out = {"schema": "judas.crafting_test_result/v1", "vendor": vendor, "evaluation_id": req.get("evaluation_id"),
           "execution_status": ev["execution_status"], "sandbox": sandbox_identity(ev["sandbox"]),
           "started_at": ev["started_at"], "finished_at": ev["finished_at"], "duration_s": ev.get("duration_s"),
           "tests": tests, "extra_tests": extra, "resources": resources,
           "readback": {"interface": "Airtable REST" if vendor == "airtable" else "monday GraphQL",
                        "performed_by": "Crafting runner on the Judas host (outside the sandbox)",
                        "checks": readback_checks(ev)},
           "container": {k: cfg[k] for k in cfg if k in ("base_id", "table_id", "table_name", "board_id", "group_id",
                                                         "group_name", "url")},
           "errors": errors, "log_excerpt": "\n".join(tail), "evidence_path": ev["evidence_path"],
           "summary": ev["summary"], "mocked": False}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"[crafting] bridge crashed: {type(e).__name__}: {e}")
        sys.exit(2)
