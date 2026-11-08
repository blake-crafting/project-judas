"""Judas -> Crafting execution interface.

    run_vendor_evaluation(vendor, sandbox=None, scenario="operational_incident_tracking", test_run_id=None)

Runs on the Judas host and drives a Crafting sandbox through the official `cs` CLI (no public
endpoint is exposed; the sandbox never needs to reach the Judas host). Steps:

  1. preflight   - cs session valid, sandbox exists and is Ready (resumes it if suspended)
  2. deliver     - copies crafting/service/ into ~/judas in the sandbox (tar over `cs exec`)
  3. setup       - python venv + pg8000 inside the sandbox
  4. configure   - container ids (no secrets) -> ~/judas/config.json; Airtable short-lived token ->
                   /tmp/judas-creds (owner-only); monday uses the Crafting personal secret
  5. start       - Northstar checkout + incident services; health checked against Postgres
  6. execute     - acceptance.py runs the ten buyer acceptance steps INSIDE the sandbox
  7. verify      - Judas host re-reads every vendor record it was told about, independently
  8. cleanup     - removes the Airtable token from the sandbox, stops the services
  9. record      - validates and saves judas.crafting_evaluation/v1 evidence

CLI: python -m crafting.runner --vendor airtable|monday|both [--run-id ID]
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import shlex
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from crafting import schema
from crafting.creds import STATE_DIR, airtable_access_token, monday_token

ORG = "demo"
TEMPLATE = "judas-incident-eval"
SANDBOXES = {"airtable": "judas-airtable", "monday": "judas-monday"}
SERVICE_DIR = Path(__file__).resolve().parent / "service"
SCENARIO = "operational_incident_tracking"
REQUIREMENT = ("When our order-processing system encounters a failure, automatically create an operational task in our "
               "workflow system. Keep status synchronized, avoid duplicate tasks when events are retried, and recover "
               "correctly from temporary API failures.")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CsError(RuntimeError):
    pass


def cs(*args: str, stdin: bytes | None = None, timeout: float = 300, org: str = ORG) -> str:
    p = subprocess.run(["cs", "-O", org, *args], input=stdin, capture_output=True, timeout=timeout)
    out = p.stdout.decode(errors="replace")
    if p.returncode != 0:
        raise CsError(f"cs {' '.join(args[:3])} failed ({p.returncode}): {(p.stderr.decode(errors='replace') or out)[-400:]}")
    return out


def sh(sandbox: str, script: str, stdin: bytes | None = None, timeout: float = 300) -> str:
    # uid 1000 = the sandbox owner account (home /home/owner); personal secrets are mounted for it
    return cs("exec", "-T", "-u", "1000", "-W", f"{sandbox}/app", "--", "bash", "-lc", script, stdin=stdin, timeout=timeout)


def service_bundle() -> tuple[bytes, str]:
    buf, h = io.BytesIO(), hashlib.sha256()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(SERVICE_DIR.iterdir()):
            if f.is_file() and f.suffix in (".py", ".sh"):
                h.update(f.name.encode() + f.read_bytes())
                tar.add(f, arcname=f.name)
    return buf.getvalue(), h.hexdigest()


def sandbox_state(sandbox: str) -> str:
    try:
        d = json.loads(cs("sandbox", "show", sandbox, "-o", "json"))
    except CsError as e:
        if "not found" in str(e).lower():
            return "NotFound"
        raise
    st = (d.get("status") or {}).get("sandbox") or {}
    return str(st.get("lifecycle_stage") or st.get("run_stage") or "Unknown")


def ensure_ready(sandbox: str, log) -> None:
    state = sandbox_state(sandbox)
    log("sandbox_state", True, state)
    if state == "NotFound":
        raise CsError(f"sandbox {sandbox} does not exist (create: cs -O {ORG} sandbox create {sandbox} -t {TEMPLATE} --access=private)")
    if "SUSPEND" in state.upper():
        cs("sandbox", "resume", sandbox, timeout=600)
        log("sandbox_resume", True, "resumed")
    for i in range(60):   # ready = the app workspace accepts a command
        try:
            sh(sandbox, "true", timeout=60)
            return
        except CsError as e:
            if i == 59:
                raise CsError(f"sandbox {sandbox} workspace not reachable: {e}")
            time.sleep(10)


def independent_readback(vendor: str, cfg: dict, expected: dict[str, str]) -> dict:
    """Re-read each vendor record from the JUDAS HOST (not the sandbox) and compare status."""
    out, ok = [], True
    for rid, want in expected.items():
        obs, err = None, None
        try:
            if vendor == "airtable":
                c = cfg["airtable"]
                req = urllib.request.Request(f"https://api.airtable.com/v0/{c['base_id']}/{c['table_id']}/{rid}",
                                             headers={"Authorization": f"Bearer {airtable_access_token()}"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    f = json.loads(r.read()).get("fields", {})
                obs = {"status": f.get("Status"), "incident_key": f.get("Incident Key"), "name": f.get("Name")}
            else:
                c = cfg["monday"]
                q = {"query": "query ($i: [ID!]) { items(ids: $i) { id name board { id } group { id } column_values { id text } } }",
                     "variables": {"i": [int(rid)]}}
                req = urllib.request.Request("https://api.monday.com/v2", data=json.dumps(q).encode(), method="POST",
                                             headers={"Authorization": monday_token() or "", "API-Version": "2025-10",
                                                      "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    items = (json.loads(r.read()).get("data") or {}).get("items") or []
                if items:
                    cv = {x["id"]: x["text"] for x in items[0]["column_values"]}
                    obs = {"status": cv.get(c["columns"]["status"]), "incident_key": cv.get(c["columns"]["key"]),
                           "name": items[0]["name"], "in_test_group": items[0]["group"]["id"] == c["group_id"]}
        except (urllib.error.URLError, OSError, ValueError) as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
        match = bool(obs) and obs.get("status") == want
        ok = ok and match
        out.append({"vendor_resource_id": rid, "expected_status": want, "observed": obs, "match": match, "error": err})
    return {"performed_by": "Judas host (outside the Crafting sandbox)",
            "method": "Airtable REST GET record by id" if vendor == "airtable" else "monday GraphQL items(ids)",
            "records": out, "verified": ok and bool(out), "checked_at": now()}


@contextlib.contextmanager
def sandbox_lock(sandbox: str, wait_s: float = 120):
    """One evaluation per sandbox at a time (each run restarts that sandbox's services). Different sandboxes run in parallel."""
    d = STATE_DIR / "locks"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"{sandbox}.lock", "w")
    t0 = time.monotonic()
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() - t0 > wait_s:
                f.close()
                raise CsError(f"sandbox {sandbox} is busy with another evaluation (waited {int(wait_s)}s)")
            time.sleep(1)
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def run_vendor_evaluation(vendor: str, sandbox: str | None = None, scenario: str = SCENARIO,
                          test_run_id: str | None = None) -> dict:
    sandbox = sandbox or SANDBOXES.get(vendor, "")
    try:
        with sandbox_lock(sandbox):
            return _run(vendor, sandbox, scenario, test_run_id)
    except CsError as e:   # lock not acquired: report as blocked, never as a vendor result
        ev = {"schema": schema.SCHEMA, "vendor": vendor, "scenario": scenario, "requirement": REQUIREMENT,
              "sandbox": sandbox, "org": ORG, "template": TEMPLATE, "test_run_id": test_run_id or "", "environment": "crafting",
              "execution_status": "blocked", "started_at": now(), "finished_at": now(), "tests": [],
              "summary": schema.summarize([]), "provenance": {"mocked": False}, "error": str(e),
              "execution_log": [{"step": "sandbox_lock", "ok": False, "at": now(), "detail": str(e)}], "evidence_path": None}
        return ev


def _run(vendor: str, sandbox: str, scenario: str, test_run_id: str | None) -> dict:
    if vendor not in SANDBOXES:
        raise ValueError(f"unknown vendor {vendor!r}")
    if scenario != SCENARIO:
        raise ValueError(f"unknown scenario {scenario!r}")
    run_id = test_run_id or datetime.now(timezone.utc).strftime("jc-%Y%m%dT%H%M%SZ")
    steps: list[dict] = []
    t_start, started = time.monotonic(), now()

    def log(step: str, ok: bool, detail: str = "") -> None:
        steps.append({"step": step, "ok": ok, "at": now(), "detail": detail[:300]})

    ev: dict = {"schema": schema.SCHEMA, "vendor": vendor, "scenario": scenario, "requirement": REQUIREMENT,
                "sandbox": sandbox, "org": ORG, "template": TEMPLATE, "test_run_id": run_id, "environment": "crafting",
                "execution_status": "error", "started_at": started, "tests": [], "execution_log": steps}
    token_written = False
    try:
        cfg = json.loads((STATE_DIR / "containers.json").read_text())
        cs("info", timeout=60)
        log("cs_session", True, "authenticated")
        ensure_ready(sandbox, log)
        bundle, sha = service_bundle()
        sh(sandbox, "rm -rf ~/judas/*.py ~/judas/*.sh; mkdir -p ~/judas && tar xzf - -C ~/judas && chmod +x ~/judas/judasctl.sh",
           stdin=bundle)
        log("deliver_code", True, f"service bundle sha256 {sha[:16]}")
        log("setup", True, sh(sandbox, "~/judas/judasctl.sh setup", timeout=600).strip()[-200:])
        sh(sandbox, "cat > ~/judas/config.json", stdin=json.dumps({"vendor": vendor, vendor: cfg[vendor]}).encode())
        log("configure", True, f"container ids for {vendor}")
        if vendor == "airtable":
            sh(sandbox, "umask 077; mkdir -p /tmp/judas-creds && cat > /tmp/judas-creds/airtable_token",
               stdin=airtable_access_token().encode())
            token_written = True
            log("credential", True, "short-lived Airtable access token placed in /tmp/judas-creds (owner-only)")
        else:
            present = sh(sandbox, "ls /run/sandbox/fs/secrets/*/judas-monday-token >/dev/null 2>&1 && echo yes || echo no").strip()
            log("credential", present == "yes", "Crafting personal secret judas-monday-token mounted" if present == "yes"
                else "Crafting secret judas-monday-token NOT mounted (sandbox must be --access=private)")
            if present != "yes":
                ev["execution_status"] = "blocked"
                raise CsError("monday credential secret not mounted in sandbox")
        log("start_services", True, sh(sandbox, "~/judas/judasctl.sh start", timeout=120).strip()[-200:])
        out = sh(sandbox, f"cd ~/judas && venv/bin/python acceptance.py --run-id {shlex.quote(run_id)} "
                          f"--sandbox {shlex.quote(sandbox)}", timeout=900)
        line = next((ln for ln in out.splitlines() if ln.startswith("JUDAS_RESULT_JSON ")), None)
        if not line:
            raise CsError(f"acceptance produced no result: {out[-300:]}")
        res = json.loads(line[len("JUDAS_RESULT_JSON "):])
        log("acceptance", True, f"{len(res['tests'])} tests executed in sandbox")
        ev.update(tests=res["tests"], service_health=res["service_health"], vendor_api=res["vendor_api"],
                  buyer_sync_log=res.get("buyer_sync_log", {}), sandbox_started_at=res["started_at"],
                  sandbox_finished_at=res["finished_at"])
        final = next((t for t in res["tests"] if t["name"] == "final_state_consistent"), None)
        expected = {p["vendor_resource_id"]: {"open": "Open", "investigating": "Investigating", "resolved": "Resolved"}
                    .get(p["internal_status"], p["internal_status"])
                    for p in ((final or {}).get("observed") or {}).get("pairs", []) if p.get("vendor_resource_id")}
        ev["judas_independent_readback"] = independent_readback(vendor, cfg, expected)
        log("independent_readback", ev["judas_independent_readback"]["verified"], f"{len(expected)} records")
        ev["execution_status"] = "completed" if len(res["tests"]) == 10 else "partial"
    except Exception as e:  # noqa: BLE001
        ev["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        if ev["tests"]:
            ev["execution_status"] = "partial"
        elif ev["execution_status"] != "blocked":   # nothing ran: a missing prerequisite, not a vendor verdict
            ev["execution_status"] = "blocked" if isinstance(e, (CsError, RuntimeError, OSError)) else "error"
        log("failure", False, ev["error"])
    finally:
        try:
            if token_written:
                sh(sandbox, "rm -f /tmp/judas-creds/airtable_token", timeout=60)
                log("cleanup_credential", True, "Airtable token removed from sandbox")
            sh(sandbox, "~/judas/judasctl.sh stop >/dev/null 2>&1 || true", timeout=60)
        except Exception as e:  # noqa: BLE001
            log("cleanup", False, str(e)[:200])
    ev["finished_at"] = now()
    ev["duration_s"] = round(time.monotonic() - t_start, 1)
    ev["summary"] = schema.summarize(ev["tests"])
    try:
        cs_version = subprocess.run(["cs", "version"], capture_output=True, timeout=20).stdout.decode().split()[0]
    except Exception:  # noqa: BLE001
        cs_version = "unknown"
    ev["provenance"] = {"runner": "crafting/runner.py on the Judas host", "interface": "Crafting CLI (cs exec) - no public endpoint",
                        "cs_cli": cs_version, "service_code_sha256": service_bundle()[1],
                        "tests_executed_in": f"Crafting sandbox {ORG}/{sandbox} workspace app",
                        "vendor_operations": "real HTTPS calls from the sandbox to the vendor's public API",
                        "mocked": False}
    ev["schema_problems"] = schema.validate(ev)
    STATE_DIR.joinpath("runs").mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "runs" / f"{run_id}-{vendor}.json"
    path.write_text(json.dumps(ev, indent=1, default=str))
    ev["evidence_path"] = str(path)
    return ev


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Northstar incident-sync evaluation in Crafting")
    ap.add_argument("--vendor", choices=["airtable", "monday", "both"], default="both")
    ap.add_argument("--run-id")
    a = ap.parse_args()
    run_id = a.run_id or datetime.now(timezone.utc).strftime("jc-%Y%m%dT%H%M%SZ")
    for v in (["airtable", "monday"] if a.vendor == "both" else [a.vendor]):
        ev = run_vendor_evaluation(v, test_run_id=run_id)
        s = ev["summary"]
        print(f"\n== {v} in {ev['sandbox']}: {ev['execution_status']}  passed={s['passed']} failed={s['failed']} "
              f"blocked={s['blocked']}  ({ev['duration_s']}s)")
        for t in ev["tests"]:
            print(f"  [{t['status']:7}] L{t['level']} {t['name']:45} id={t.get('vendor_resource_id')}"
                  + (f"  ERR {t['error'][:90]}" if t.get("error") else ""))
        rb = ev.get("judas_independent_readback") or {}
        print(f"  Judas-host independent readback: {'verified' if rb.get('verified') else 'NOT verified'} "
              f"({len(rb.get('records', []))} records)")
        if ev.get("error"):
            print(f"  error: {ev['error']}")
        print(f"  evidence: {ev['evidence_path']}")


if __name__ == "__main__":
    main()
