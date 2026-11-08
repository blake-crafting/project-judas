"""Northstar Commerce (fictional buyer) - two small services that run inside a Crafting sandbox.

  checkout  :8081  order-processing service. A failed payment emits an incident event over HTTP.
  incident  :8080  incident-sync service. Stores incident state in Postgres (`db` dependency) and
                   syncs it to the configured workflow vendor (Airtable or monday.com).

Run:  python3 northstar.py checkout | incident
Config (no secrets): ~/judas/config.json, written by the Judas runner.

Integration behaviour owned by the BUYER (this code, not the vendor):
  - internal idempotency: one incident per event_key (Postgres unique constraint)
  - retries with backoff on transient failures
  - reconcile-before-retry: after a failed create, search the vendor by Incident Key before
    creating again (needs a vendor lookup-by-field capability)
  - fault injection hook for controlled tests (POST /admin/faults)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pg8000.native

import vendors as V

HOME = os.path.expanduser("~/judas")
CFG = json.load(open(os.path.join(HOME, "config.json"))) if os.path.exists(os.path.join(HOME, "config.json")) else {}
INCIDENT_URL = os.environ.get("INCIDENT_URL", "http://localhost:8080")
MAX_ATTEMPTS = 4
RECONCILE_POLLS = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, test_run_id TEXT, amount NUMERIC, status TEXT,
  failure_code TEXT, incident_id INT, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS incidents (id SERIAL PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, test_run_id TEXT,
  order_id TEXT, failure_code TEXT, title TEXT, severity TEXT, status TEXT, vendor TEXT, vendor_resource_id TEXT,
  sync_state TEXT, last_error TEXT, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now());
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS occurrence_count INT DEFAULT 1;
CREATE TABLE IF NOT EXISTS sync_log (id SERIAL PRIMARY KEY, incident_id INT, op TEXT, attempt INT, outcome TEXT,
  detail TEXT, at TIMESTAMPTZ DEFAULT now());
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def db() -> pg8000.native.Connection:
    return pg8000.native.Connection(user=os.environ.get("DB_USER", "postgres"),
                                    password=os.environ.get("DB_PASSWORD", "batman"),
                                    host=os.environ.get("DB_HOST", "db"), database=os.environ.get("DB_NAME", "northstar"))


def rows(c, sql: str, **kw) -> list[dict]:
    out = c.run(sql, **kw)
    cols = [d["name"] for d in c.columns] if c.columns else []
    return [dict(zip(cols, r)) for r in out or []]


def init_db() -> None:
    c = db()
    c.run("SELECT pg_advisory_lock(4242)")   # both services start together; serialize schema creation
    try:
        for stmt in filter(str.strip, SCHEMA.split(";")):
            c.run(stmt)
    finally:
        c.run("SELECT pg_advisory_unlock(4242)")
        c.close()


# ---------------------------------------------------------------- incident service

FAULTS: dict = {"mode": None, "remaining": 0}
FAULT_LOCK = threading.Lock()
KEY_LOCKS: dict[str, threading.Lock] = {}
KEY_LOCKS_LOCK = threading.Lock()


def take_fault() -> str | None:
    with FAULT_LOCK:
        if FAULTS["remaining"] > 0:
            FAULTS["remaining"] -= 1
            return FAULTS["mode"]
    return None


def log(c, incident_id: int, op: str, attempt: int, outcome: str, detail: str = "") -> None:
    c.run("INSERT INTO sync_log (incident_id, op, attempt, outcome, detail) VALUES (:i, :o, :a, :r, :d)",
          i=incident_id, o=op, a=attempt, r=outcome, d=detail[:500])


def vendor():
    return V.connector(CFG["vendor"], CFG)


def sync_create(c, inc: dict) -> dict:
    conn, attempts, reconciled, polls = vendor(), 0, False, 0
    rid = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        try:
            if attempt > 1:   # a previous attempt may have reached the vendor: look before creating again
                for p in range(RECONCILE_POLLS):
                    polls += 1
                    found = conn.find_by_key(inc["event_key"])
                    if found:
                        rid, reconciled = found[0], True
                        log(c, inc["id"], "reconcile", attempt, "found_existing", f"{len(found)} match(es)")
                        break
                    V.sleep(1.5)
                if rid:
                    break
                log(c, inc["id"], "reconcile", attempt, "not_found", f"after {RECONCILE_POLLS} polls")
            mode = take_fault()
            if mode == "error_before_send":
                raise V.TransientError("INJECTED: connection reset before request was sent")
            rid = conn.create(inc)
            if mode == "timeout_after_send":
                log(c, inc["id"], "create", attempt, "sent_response_dropped", f"vendor id {rid} discarded by injector")
                rid = None
                raise V.TransientError("INJECTED: request reached vendor but response was lost (timeout)")
            log(c, inc["id"], "create", attempt, "ok", rid)
            break
        except V.BlockedError as e:
            log(c, inc["id"], "create", attempt, "blocked", str(e))
            c.run("UPDATE incidents SET sync_state='blocked', last_error=:e, updated_at=now() WHERE id=:i", e=str(e)[:500], i=inc["id"])
            return {"ok": False, "blocked": True, "error": str(e), "attempts": attempts}
        except V.TransientError as e:
            log(c, inc["id"], "create", attempt, "transient_error", str(e))
            V.sleep(V.wait_s(attempt))
        except V.VendorError as e:
            log(c, inc["id"], "create", attempt, "error", str(e))
            c.run("UPDATE incidents SET sync_state='error', last_error=:e, updated_at=now() WHERE id=:i", e=str(e)[:500], i=inc["id"])
            return {"ok": False, "error": str(e), "attempts": attempts}
    if not rid:
        c.run("UPDATE incidents SET sync_state='error', last_error='retries exhausted', updated_at=now() WHERE id=:i", i=inc["id"])
        return {"ok": False, "error": "retries exhausted", "attempts": attempts, "reconcile_polls": polls}
    c.run("UPDATE incidents SET vendor_resource_id=:r, sync_state='synced', last_error=NULL, updated_at=now() WHERE id=:i",
          r=rid, i=inc["id"])
    return {"ok": True, "vendor_resource_id": rid, "attempts": attempts, "reconciled": reconciled, "reconcile_polls": polls}


def handle_event(ev: dict) -> dict:
    key = ev["event_key"]
    with KEY_LOCKS_LOCK:
        lock = KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:
        c = db()
        try:
            c.run("INSERT INTO incidents (event_key, test_run_id, order_id, failure_code, title, severity, status, vendor, sync_state) "
                  "VALUES (:k, :r, :o, :f, :t, :s, 'open', :v, 'pending') ON CONFLICT (event_key) DO NOTHING",
                  k=key, r=ev["test_run_id"], o=ev["order_id"], f=ev["failure_code"],
                  t=f"[{ev['severity']}] Checkout failure {ev['failure_code']} on order {ev['order_id']}",
                  s=ev["severity"], v=CFG.get("vendor"))
            inc = rows(c, "SELECT * FROM incidents WHERE event_key=:k", k=key)[0]
            if inc["vendor_resource_id"]:
                c.run("UPDATE incidents SET occurrence_count = occurrence_count + 1, updated_at=now() WHERE id=:i", i=inc["id"])
                log(c, inc["id"], "event", 0, "duplicate_ignored", key)
                return {"incident_id": inc["id"], "vendor_resource_id": inc["vendor_resource_id"], "created": False,
                        "duplicate": True, "attempts": 0}
            res = sync_create(c, inc)
            return {"incident_id": inc["id"], "created": res.get("ok", False), "duplicate": False, **res}
        finally:
            c.close()


def set_status(incident_id: int, status: str) -> dict:
    c = db()
    try:
        inc = rows(c, "SELECT * FROM incidents WHERE id=:i", i=incident_id)
        if not inc:
            return {"ok": False, "error": "no such incident"}
        inc = inc[0]
        c.run("UPDATE incidents SET status=:s, updated_at=now() WHERE id=:i", s=status, i=incident_id)
        if not inc["vendor_resource_id"]:
            return {"ok": False, "internal_status": status, "error": "incident not linked to a vendor record"}
        conn = vendor()
        for attempt in range(1, 3):
            try:
                conn.update_status(inc["vendor_resource_id"], status)
                log(c, incident_id, "update_status", attempt, "ok", status)
                return {"ok": True, "internal_status": status, "vendor_resource_id": inc["vendor_resource_id"], "attempts": attempt}
            except V.TransientError as e:
                log(c, incident_id, "update_status", attempt, "transient_error", str(e))
                V.sleep(V.wait_s(attempt))
            except V.VendorError as e:
                log(c, incident_id, "update_status", attempt, "blocked" if isinstance(e, V.BlockedError) else "error", str(e))
                return {"ok": False, "internal_status": status, "blocked": isinstance(e, V.BlockedError), "error": str(e)}
        return {"ok": False, "internal_status": status, "error": "retries exhausted"}
    finally:
        c.close()


def get_incident(incident_id: int) -> dict:
    c = db()
    try:
        inc = rows(c, "SELECT id, event_key, order_id, status, severity, occurrence_count, vendor, vendor_resource_id, sync_state, last_error "
                      "FROM incidents WHERE id=:i", i=incident_id)
        log_rows = rows(c, "SELECT op, attempt, outcome, detail, at::text FROM sync_log WHERE incident_id=:i ORDER BY id", i=incident_id)
        return {**inc[0], "sync_log": log_rows} if inc else {}
    finally:
        c.close()


# ---------------------------------------------------------------- checkout service

def checkout(body: dict) -> dict:
    oid, run, pay = body["order_id"], body["test_run_id"], body.get("payment", "ok")
    c = db()
    try:
        failed = pay != "ok"
        code = {"declined": "PAYMENT_DECLINED", "gateway_timeout": "PAYMENT_GATEWAY_TIMEOUT"}.get(pay, "PAYMENT_ERROR") if failed else None
        c.run("INSERT INTO orders (order_id, test_run_id, amount, status, failure_code) VALUES (:o, :r, :a, :s, :f) "
              "ON CONFLICT (order_id) DO UPDATE SET status=EXCLUDED.status, failure_code=EXCLUDED.failure_code",
              o=oid, r=run, a=body.get("amount", 0), s="payment_failed" if failed else "paid", f=code)
        if not failed:
            return {"order_id": oid, "order_status": "paid", "incident": None}
        inc = emit(oid, run, code, body.get("severity", "P2"))
        if inc.get("incident_id"):
            c.run("UPDATE orders SET incident_id=:i WHERE order_id=:o", i=inc["incident_id"], o=oid)
        return {"order_id": oid, "order_status": "payment_failed", "failure_code": code, "incident": inc}
    finally:
        c.close()


def emit(oid: str, run: str, code: str, severity: str) -> dict:
    ev = {"event_key": f"{run}:{oid}:{code}", "test_run_id": run, "order_id": oid, "failure_code": code,
          "severity": severity, "occurred_at": now(), "source": "northstar-checkout"}
    req = urllib.request.Request(INCIDENT_URL + "/events/order-failure", data=json.dumps(ev).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def redeliver(oid: str) -> dict:
    c = db()
    try:
        o = rows(c, "SELECT * FROM orders WHERE order_id=:o", o=oid)
        if not o or not o[0]["failure_code"]:
            return {"error": "no failed order"}
        o = o[0]
        inc = rows(c, "SELECT severity FROM incidents WHERE id=:i", i=o["incident_id"]) if o["incident_id"] else []
        return {"order_id": oid, "redelivered": True,
                "incident": emit(oid, o["test_run_id"], o["failure_code"], inc[0]["severity"] if inc else "P2")}
    finally:
        c.close()


def get_order(oid: str) -> dict:
    c = db()
    try:
        o = rows(c, "SELECT order_id, test_run_id, status, failure_code, incident_id FROM orders WHERE order_id=:o", o=oid)
        return o[0] if o else {}
    finally:
        c.close()


# ---------------------------------------------------------------- HTTP plumbing

class Handler(BaseHTTPRequestHandler):
    role = "incident"

    def log_message(self, fmt, *args):  # one line per request to stderr (daemon log); never bodies
        sys.stderr.write(f"{now()} {self.role} {self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    def _send(self, code: int, obj) -> None:
        b = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        try:
            p = self.path.rstrip("/")
            if p == "/health":
                c = db(); c.run("SELECT 1"); c.close()
                return self._send(200, {"ok": True, "service": self.role, "vendor": CFG.get("vendor"), "db": "ok"})
            if self.role == "incident" and p.startswith("/incidents/"):
                return self._send(200, get_incident(int(p.split("/")[2])))
            if self.role == "checkout" and p.startswith("/orders/"):
                return self._send(200, get_order(p.split("/")[2]))
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            p, body = self.path.rstrip("/"), self._body()
            if self.role == "incident" and p == "/events/order-failure":
                return self._send(200, handle_event(body))
            if self.role == "incident" and p == "/admin/faults":
                with FAULT_LOCK:
                    FAULTS.update(mode=body.get("mode"), remaining=int(body.get("count", 1)))
                return self._send(200, dict(FAULTS))
            if self.role == "checkout" and p == "/checkout":
                return self._send(200, checkout(body))
            if self.role == "checkout" and p.startswith("/orders/") and p.endswith("/redeliver"):
                return self._send(200, redeliver(p.split("/")[2]))
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_PATCH(self):
        try:
            p, body = self.path.rstrip("/"), self._body()
            if self.role == "incident" and p.startswith("/incidents/"):
                return self._send(200, set_status(int(p.split("/")[2]), body["status"]))
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main() -> None:
    role = sys.argv[1]
    init_db()
    Handler.role = role
    port = 8080 if role == "incident" else 8081
    sys.stderr.write(f"{now()} {role} listening on :{port} vendor={CFG.get('vendor')}\n")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
