"""Northstar acceptance tests - runs INSIDE the Crafting sandbox against the live local services.

Drives the buyer's real application flow over HTTP (checkout :8081 -> incident :8080 -> vendor API),
then verifies vendor persistence with a GET-by-id and a lookup-by-key against the vendor API, and
internal state from the incident service (backed by Postgres).

Prints ONE JSON document (judas.crafting_evaluation/v1 "tests" section) on stdout. Never prints
credentials. Usage: python3 acceptance.py --run-id RUN --sandbox NAME
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
import urllib.request
from datetime import datetime, timezone

import vendors as V
from northstar import CFG

INC, CHK = "http://localhost:8080", "http://localhost:8081"
OWN_VENDOR = "vendor"            # behaviour the vendor product provides
OWN_BUYER = "buyer_connector"    # behaviour Northstar's own integration code provides


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def call(method: str, url: str, body: dict | None = None, timeout: float = 150) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class Prereq(Exception):
    """An earlier step did not produce what this step needs (reported as blocked, not failed)."""


class Suite:
    def need(self, key: str):
        v = self.ctx.get(key)
        if not v:
            raise Prereq(f"prerequisite missing from an earlier step: {key}")
        return v

    def __init__(self, run_id: str):
        self.run, self.tests, self.ctx = run_id, [], {}
        self.vendor = V.connector(CFG["vendor"], CFG)

    def test(self, name: str, level: int, operation: str, responsibility: str, verification: str, input_: dict, fn):
        t0, started = time.monotonic(), now()
        rec = {"name": name, "level": level, "operation": operation, "responsibility": responsibility,
               "verification_method": verification, "input": input_, "started_at": started,
               "vendor_resource_id": None, "readback_verified": False, "expected": None, "observed": None,
               "status": "failed", "error": None}
        try:
            fn(rec)
        except V.BlockedError as e:
            rec.update(status="blocked", error=f"BLOCKED (not a capability verdict): {e}")
        except Prereq as e:
            rec.update(status="blocked", error=str(e))
        except Exception as e:  # noqa: BLE001
            rec.update(status="failed", error=f"{type(e).__name__}: {str(e)[:300]}",
                       trace=traceback.format_exc(limit=2)[-400:])
        rec["duration_ms"] = int((time.monotonic() - t0) * 1000)
        self.tests.append(rec)
        return rec

    @staticmethod
    def verdict(rec: dict, expected, observed, ok: bool, blocked_reason: str | None = None) -> None:
        rec["expected"], rec["observed"] = expected, observed
        if blocked_reason:
            rec.update(status="blocked", error=blocked_reason)
        else:
            rec["status"] = "passed" if ok else "failed"

    def order(self, n: int) -> str:
        return f"NS-{self.run[-6:]}-{n}"

    # --------------------------------------------------------------- the ten acceptance steps
    def run_all(self) -> None:
        o1 = self.order(1)

        def t1(rec):
            r = call("POST", CHK + "/checkout", {"order_id": o1, "test_run_id": self.run, "amount": 129.0,
                                                 "payment": "declined", "severity": "P1"})
            self.ctx["r1"] = r
            inc = r.get("incident") or {}
            order = call("GET", f"{CHK}/orders/{o1}")
            ok = r.get("order_status") == "payment_failed" and bool(inc.get("incident_id")) and order.get("incident_id") == inc.get("incident_id")
            self.verdict(rec, {"order_status": "payment_failed", "incident_event_delivered": True},
                         {"order_status": r.get("order_status"), "incident_id": inc.get("incident_id"),
                          "order_row_links_incident": order.get("incident_id") == inc.get("incident_id")}, ok)
        self.test("generate_order_failure", 3, "POST checkout (payment declined) -> incident event over HTTP", OWN_BUYER,
                  "checkout + incident service responses and Postgres order row", {"order_id": o1, "payment": "declined"}, t1)

        def t2(rec):
            inc = self.need("r1").get("incident") or {}
            if inc.get("blocked"):
                return self.verdict(rec, {"created": True}, inc, False, f"BLOCKED (not a capability verdict): {inc.get('error')}")
            rec["vendor_resource_id"] = inc.get("vendor_resource_id")
            self.ctx["rid1"], self.ctx["iid1"] = inc.get("vendor_resource_id"), inc.get("incident_id")
            self.verdict(rec, {"created": True, "vendor_resource_id": "non-empty"},
                         {"created": inc.get("created"), "vendor_resource_id": inc.get("vendor_resource_id"),
                          "attempts": inc.get("attempts"), "error": inc.get("error")},
                         bool(inc.get("created") and inc.get("vendor_resource_id")))
        self.test("create_vendor_task", 1, f"{self.vendor.api}: create record", OWN_VENDOR,
                  "vendor create response returned an object id", {"incident": "from step 1"}, t2)

        def t3(rec):
            rid = self.need("rid1")
            rec["vendor_resource_id"] = rid
            got = self.vendor.read(rid)
            exp = {"event_key": f"{self.run}:{o1}:PAYMENT_DECLINED", "status": "Open", "order_id": o1, "severity": "P1"}
            obs = {k: got.get(k) for k in exp}
            rec["readback_verified"] = obs == exp
            self.verdict(rec, exp, obs, obs == exp)
        self.test("readback_created_task", 2, f"{self.vendor.api}: GET record by id", OWN_VENDOR,
                  "independent GET by vendor id, field-by-field compare", {}, t3)

        def t4(rec):
            r = call("PATCH", f"{INC}/incidents/{self.need('iid1')}", {"status": "investigating"})
            self.ctx["r4"] = r
            internal = call("GET", f"{INC}/incidents/{self.ctx['iid1']}")
            self.verdict(rec, {"internal_status": "investigating"}, {"internal_status": internal.get("status")},
                         internal.get("status") == "investigating")
        self.test("update_internal_status", 3, "PATCH incident status=investigating (buyer system of record)", OWN_BUYER,
                  "incident service GET (Postgres row)", {"status": "investigating"}, t4)

        def t5(rec):
            r = self.need("r4")
            rec["vendor_resource_id"] = r.get("vendor_resource_id")
            if r.get("blocked"):
                return self.verdict(rec, {"vendor_synced": True}, r, False, f"BLOCKED (not a capability verdict): {r.get('error')}")
            self.verdict(rec, {"vendor_synced": True}, {"vendor_synced": r.get("ok"), "attempts": r.get("attempts"),
                                                        "error": r.get("error")}, bool(r.get("ok")))
        self.test("propagate_status_to_vendor", 1, f"{self.vendor.api}: update record status", OWN_VENDOR,
                  "vendor update call succeeded", {}, t5)

        def t6(rec):
            rid = self.need("rid1")
            rec["vendor_resource_id"] = rid
            got = self.vendor.read(rid)
            rec["readback_verified"] = got.get("status") == "Investigating"
            self.verdict(rec, {"status": "Investigating"}, {"status": got.get("status")}, rec["readback_verified"])
        self.test("readback_updated_status", 2, f"{self.vendor.api}: GET record by id", OWN_VENDOR,
                  "independent GET by vendor id after update", {}, t6)

        def t7(rec):
            self.need("rid1")
            resp = [call("POST", f"{CHK}/orders/{o1}/redeliver", {}).get("incident") or {} for _ in range(2)]
            key = f"{self.run}:{o1}:PAYMENT_DECLINED"
            matches = self.vendor.find_by_key(key)
            internal = call("GET", f"{INC}/incidents/{self.ctx['iid1']}")
            rec["vendor_resource_id"] = resp[-1].get("vendor_resource_id")
            exp = {"deliveries": 3, "same_vendor_id": True, "created_again": False, "internal_occurrence_count": 3,
                   "vendor_records_with_key": 1}
            obs = {"deliveries": 1 + len(resp), "same_vendor_id": all(x.get("vendor_resource_id") == self.ctx["rid1"] for x in resp),
                   "created_again": any(x.get("created") for x in resp), "internal_occurrence_count": internal.get("occurrence_count"),
                   "vendor_records_with_key": len(matches)}
            rec["readback_verified"] = len(matches) == 1
            self.verdict(rec, exp, obs, obs == exp)
        self.test("duplicate_event_no_duplicate_task", 3, "deliver the SAME order-failure event 3x in total (at-least-once delivery)",
                  OWN_BUYER, "service response + vendor lookup by Incident Key (count must be 1)", {"order_id": o1}, t7)

        o2 = self.order(2)

        def t8(rec):
            call("POST", INC + "/admin/faults", {"mode": "error_before_send", "count": 1})
            r = call("POST", CHK + "/checkout", {"order_id": o2, "test_run_id": self.run, "amount": 54.0,
                                                 "payment": "gateway_timeout", "severity": "P2"})
            inc = r.get("incident") or {}
            rec["vendor_resource_id"] = inc.get("vendor_resource_id")
            matches = self.vendor.find_by_key(f"{self.run}:{o2}:PAYMENT_GATEWAY_TIMEOUT") if inc.get("vendor_resource_id") else []
            self.ctx["rid2"], self.ctx["iid2"] = inc.get("vendor_resource_id"), inc.get("incident_id")
            rec["readback_verified"] = len(matches) == 1
            exp = {"created": True, "attempts_at_least": 2, "vendor_records_with_key": 1}
            obs = {"created": inc.get("created"), "attempts": inc.get("attempts"), "vendor_records_with_key": len(matches),
                   "error": inc.get("error")}
            self.verdict(rec, exp, obs, bool(inc.get("created")) and (inc.get("attempts") or 0) >= 2 and len(matches) == 1)
        self.test("recover_from_transient_network_failure", 3,
                  "INJECTED connection failure before send (1x), connector retries with backoff", OWN_BUYER,
                  "service attempts count + vendor lookup by Incident Key", {"order_id": o2, "fault": "error_before_send x1"}, t8)

        o3 = self.order(3)

        def t9(rec):
            call("POST", INC + "/admin/faults", {"mode": "timeout_after_send", "count": 1})
            r = call("POST", CHK + "/checkout", {"order_id": o3, "test_run_id": self.run, "amount": 310.0,
                                                 "payment": "declined", "severity": "P1"})
            inc = r.get("incident") or {}
            key = f"{self.run}:{o3}:PAYMENT_DECLINED"
            matches = self.vendor.find_by_key(key)
            rec["vendor_resource_id"] = inc.get("vendor_resource_id")
            self.ctx["rid3"], self.ctx["iid3"] = inc.get("vendor_resource_id"), inc.get("incident_id")
            rec["readback_verified"] = len(matches) == 1
            exp = {"created": True, "reconciled_existing_record": True, "vendor_records_with_key": 1}
            obs = {"created": inc.get("created"), "reconciled_existing_record": inc.get("reconciled"),
                   "reconcile_polls": inc.get("reconcile_polls"), "attempts": inc.get("attempts"),
                   "vendor_records_with_key": len(matches), "vendor_ids_with_key": matches, "error": inc.get("error")}
            self.verdict(rec, exp, obs, bool(inc.get("created")) and bool(inc.get("reconciled")) and len(matches) == 1)
        self.test("recover_from_lost_response_without_duplicate", 3,
                  "INJECTED timeout AFTER the vendor received the create (1x); connector must find the record by key before retrying",
                  f"{OWN_BUYER} + {OWN_VENDOR} (lookup-by-field)",
                  "vendor lookup by Incident Key (count must be 1)", {"order_id": o3, "fault": "timeout_after_send x1"}, t9)

        def t10(rec):
            r = call("PATCH", f"{INC}/incidents/{self.need('iid1')}", {"status": "resolved"})
            pairs, ok = [], bool(r.get("ok"))
            for iid, rid in ((self.ctx.get("iid1"), self.ctx.get("rid1")), (self.ctx.get("iid2"), self.ctx.get("rid2")),
                             (self.ctx.get("iid3"), self.ctx.get("rid3"))):
                if not iid:
                    ok = False
                    continue
                internal = call("GET", f"{INC}/incidents/{iid}")
                remote = self.vendor.read(rid) if rid else {}
                want = V.STATUS_LABEL.get(internal.get("status"))
                match = bool(rid) and remote.get("status") == want and internal.get("sync_state") == "synced"
                ok = ok and match
                pairs.append({"incident_id": iid, "vendor_resource_id": rid, "internal_status": internal.get("status"),
                              "vendor_status": remote.get("status"), "sync_state": internal.get("sync_state"), "match": match})
            rec["vendor_resource_id"] = self.ctx.get("rid1")
            rec["readback_verified"] = ok
            self.verdict(rec, {"all_incidents_match_vendor": True, "incident_1": "Resolved"}, {"pairs": pairs}, ok)
        self.test("final_state_consistent", 2, "resolve incident 1, then compare every internal incident to its vendor record",
                  f"{OWN_BUYER} + {OWN_VENDOR}", "Postgres state vs vendor GET by id, per incident",
                  {"status": "resolved"}, t10)

        # attach the buyer-side sync log for each incident (provenance for the retry/reconcile claims)
        self.ctx["sync_logs"] = {str(i): call("GET", f"{INC}/incidents/{i}").get("sync_log")
                                 for i in (self.ctx.get("iid1"), self.ctx.get("iid2"), self.ctx.get("iid3")) if i}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--sandbox", required=True)
    a = ap.parse_args()
    started = now()
    health = {}
    for name, url in (("incident", INC), ("checkout", CHK)):
        try:
            health[name] = call("GET", url + "/health", timeout=10)
        except Exception as e:  # noqa: BLE001
            health[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    s = Suite(a.run_id)
    if all(h.get("ok") for h in health.values()):
        s.run_all()
    out = {"vendor": CFG.get("vendor"), "sandbox": a.sandbox, "test_run_id": a.run_id, "started_at": started,
           "finished_at": now(), "service_health": health, "vendor_api": s.vendor.api, "tests": s.tests,
           "buyer_sync_log": s.ctx.get("sync_logs", {})}
    print("JUDAS_RESULT_JSON " + json.dumps(out, default=str))


if __name__ == "__main__":
    main()
