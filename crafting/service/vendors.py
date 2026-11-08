"""Vendor connectors used INSIDE the Crafting sandbox by Northstar's incident-sync service.

These are the buyer's own integration code (written by the hackathon team), calling each vendor's
public REST/GraphQL API. Credentials are read from files on every call and are never logged:
  - Airtable: short-lived OAuth access token written by the Judas runner to /tmp/judas-creds/airtable_token
  - monday.com: Crafting personal secret `judas-monday-token`, mounted at /run/sandbox/fs/secrets/<owner-id>/

Errors are classified so the evaluator never mistakes a permission or plan limit for a missing
product capability:
  BlockedError   - auth / permission / plan / rate limit (vendor capability NOT established)
  TransientError - network failure or timeout (retryable)
  VendorError    - any other vendor-side rejection
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

CRED_PATHS = {
    "airtable": os.environ.get("JUDAS_AIRTABLE_TOKEN_FILE", "/tmp/judas-creds/airtable_token"),
    "monday": os.environ.get("JUDAS_MONDAY_TOKEN_FILE", "/run/sandbox/fs/secrets/owner/judas-monday-token"),
}

STATUS_LABEL = {"open": "Open", "investigating": "Investigating", "resolved": "Resolved"}


class VendorError(RuntimeError):
    pass


class BlockedError(VendorError):
    pass


class TransientError(VendorError):
    pass


def _token(vendor: str) -> str:
    path = CRED_PATHS[vendor]
    if vendor == "monday" and not os.path.exists(path):   # Crafting mounts personal secrets under the owner's id
        import glob
        path = next(iter(sorted(glob.glob("/run/sandbox/fs/secrets/*/judas-monday-token"))), path)
    try:
        with open(path) as f:
            tok = f.read().strip()
    except OSError:
        raise BlockedError(f"{vendor} credential not present in sandbox ({os.path.basename(path)})")
    if not tok:
        raise BlockedError(f"{vendor} credential file is empty")
    return tok


def _http(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 20.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read()[:300].decode(errors="replace")
        if e.code in (401, 403):
            raise BlockedError(f"HTTP {e.code} (auth/permission): {text}")
        if e.code == 429:
            raise BlockedError(f"HTTP 429 (rate/plan limit): {text}")
        if e.code >= 500:
            raise TransientError(f"HTTP {e.code}: {text}")
        raise VendorError(f"HTTP {e.code}: {text}")
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise TransientError(f"network: {type(e).__name__}: {e}")


class Airtable:
    name = "airtable"
    api = "Airtable REST API (api.airtable.com/v0)"

    def __init__(self, cfg: dict):
        self.base, self.table = cfg["base_id"], cfg["table_id"]
        self.url = f"https://api.airtable.com/v0/{self.base}/{self.table}"

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {_token('airtable')}"}

    @staticmethod
    def _fields(inc: dict) -> dict:
        return {"Name": inc["title"], "Incident Key": inc["event_key"], "Status": STATUS_LABEL[inc["status"]],
                "Severity": inc["severity"], "Order ID": inc["order_id"], "Test Run": inc["test_run_id"]}

    def create(self, inc: dict) -> str:
        r = _http("POST", self.url, self._h(), {"records": [{"fields": self._fields(inc)}], "typecast": True})
        return r["records"][0]["id"]

    def find_by_key(self, key: str) -> list[str]:
        formula = "{Incident Key}='" + key.replace("'", "\\'") + "'"
        r = _http("GET", self.url + "?" + urllib.parse.urlencode({"filterByFormula": formula}), self._h())
        return [x["id"] for x in r.get("records", [])]

    def update_status(self, rid: str, status: str) -> None:
        _http("PATCH", f"{self.url}/{rid}", self._h(), {"fields": {"Status": STATUS_LABEL[status]}, "typecast": True})

    def read(self, rid: str) -> dict:
        f = _http("GET", f"{self.url}/{rid}", self._h()).get("fields", {})
        return {"title": f.get("Name"), "event_key": f.get("Incident Key"), "status": f.get("Status"),
                "severity": f.get("Severity"), "order_id": f.get("Order ID")}


class Monday:
    name = "monday"
    api = "monday.com GraphQL API (api.monday.com/v2)"

    def __init__(self, cfg: dict):
        self.board, self.group, self.cols = int(cfg["board_id"]), cfg["group_id"], cfg["columns"]

    def _q(self, query: str, variables: dict | None = None) -> dict:
        r = _http("POST", "https://api.monday.com/v2",
                  {"Authorization": _token("monday"), "API-Version": "2025-10"},
                  {"query": query, "variables": variables or {}})
        if r.get("errors") or r.get("error_message"):
            msg = json.dumps(r.get("errors") or r.get("error_message"))[:300]
            code = json.dumps(r).lower()
            if any(s in code for s in ("complexity", "rate limit", "daily limit", "unauthorized", "not authenticated",
                                       "usercannot", "permission")):
                raise BlockedError(f"monday API: {msg}")
            raise VendorError(f"monday API: {msg}")
        return r.get("data") or {}

    def _values(self, inc: dict, status_only: bool = False) -> str:
        c = self.cols
        v = {c["status"]: {"label": STATUS_LABEL[inc["status"]]}}
        if not status_only:
            v.update({c["key"]: inc["event_key"], c["severity"]: {"label": inc["severity"]},
                      c["order"]: inc["order_id"], c["run"]: inc["test_run_id"]})
        return json.dumps(v)

    def create(self, inc: dict) -> str:
        d = self._q("mutation ($b: ID!, $g: String!, $n: String!, $v: JSON!) { create_item(board_id: $b, group_id: $g, "
                    "item_name: $n, column_values: $v, create_labels_if_missing: true) { id } }",
                    {"b": self.board, "g": self.group, "n": inc["title"], "v": self._values(inc)})
        return str(d["create_item"]["id"])

    def find_by_key(self, key: str) -> list[str]:
        d = self._q("query ($b: ID!, $c: String!, $k: String!) { items_page_by_column_values(board_id: $b, limit: 25, "
                    "columns: [{column_id: $c, column_values: [$k]}]) { items { id } } }",
                    {"b": self.board, "c": self.cols["key"], "k": key})
        return [str(i["id"]) for i in (d.get("items_page_by_column_values") or {}).get("items", [])]

    def update_status(self, rid: str, status: str) -> None:
        self._q("mutation ($b: ID!, $i: ID!, $v: JSON!) { change_multiple_column_values(board_id: $b, item_id: $i, "
                "column_values: $v, create_labels_if_missing: true) { id } }",
                {"b": self.board, "i": int(rid), "v": self._values({"status": status}, status_only=True)})

    def read(self, rid: str) -> dict:
        d = self._q("query ($i: [ID!]) { items(ids: $i) { id name column_values { id text } } }", {"i": [int(rid)]})
        items = d.get("items") or []
        if not items:
            return {}
        cv = {x["id"]: x["text"] for x in items[0]["column_values"]}
        c = self.cols
        return {"title": items[0]["name"], "event_key": cv.get(c["key"]), "status": cv.get(c["status"]),
                "severity": cv.get(c["severity"]), "order_id": cv.get(c["order"])}


def connector(vendor: str, cfg: dict):
    return {"airtable": Airtable, "monday": Monday}[vendor](cfg[vendor])


def wait_s(attempt: int) -> float:
    return min(0.5 * (2 ** attempt), 4.0)


def sleep(s: float) -> None:
    time.sleep(s)
