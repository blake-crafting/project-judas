"""Local (Judas host) credential helpers for the Crafting evaluation runner.

Credentials stay on the Judas host. The runner hands a sandbox only what one run needs:
  - Airtable: the current short-lived OAuth access token (refreshed HERE if expired, so the
    rotating refresh token never leaves this machine).
  - monday.com: nothing per run; the sandbox reads the Crafting personal secret `judas-monday-token`.
Nothing in this module prints or logs a credential value.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREDS_ROOT = Path(os.environ.get("JUDAS_CREDS_ROOT", ROOT))
AIRTABLE_STORE = CREDS_ROOT / "data" / "oauth" / "airtable.json"
STATE_DIR = CREDS_ROOT / "data" / "crafting"


def env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    f = CREDS_ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'") or None
    return None


def _get(url: str, headers: dict | None = None) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def _token_endpoint() -> str:
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        code, meta = _get("https://mcp.airtable.com" + path)
        if code == 200 and meta.get("authorization_servers"):
            asrv = meta["authorization_servers"][0].rstrip("/")
            code, am = _get(asrv + "/.well-known/oauth-authorization-server")
            if code == 200 and am.get("token_endpoint"):
                return am["token_endpoint"]
    return "https://airtable.com/oauth2/v1/token"


def airtable_access_token() -> str:
    """A working Airtable access token. Refreshes (and saves, owner-only) if the stored one is rejected."""
    if not AIRTABLE_STORE.exists():
        raise RuntimeError("Airtable login not found on the Judas host (run scripts/airtable_login.py)")
    d = json.loads(AIRTABLE_STORE.read_text())
    tok = (d.get("tokens") or {}).get("access_token")
    if tok and _get("https://api.airtable.com/v0/meta/whoami", {"Authorization": f"Bearer {tok}"})[0] == 200:
        return tok
    refresh, client_id = (d.get("tokens") or {}).get("refresh_token"), (d.get("client") or {}).get("client_id")
    if not refresh or not client_id:
        raise RuntimeError("Airtable login expired and cannot be refreshed (run scripts/airtable_login.py)")
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id}).encode()
    req = urllib.request.Request(_token_endpoint(), data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            new = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Airtable token refresh failed (HTTP {e.code}); run scripts/airtable_login.py")
    t = dict(d["tokens"])
    t.update({k: new[k] for k in ("access_token", "token_type", "expires_in", "scope", "refresh_token") if new.get(k)})
    d["tokens"] = t
    tmp = AIRTABLE_STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.chmod(tmp, 0o600)
    os.replace(tmp, AIRTABLE_STORE)
    return t["access_token"]


def monday_token() -> str | None:
    return env_value("MONDAY_TOKEN")
