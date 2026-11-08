"""OAuth for Airtable's official MCP (authorization code + PKCE, dynamic client registration).

Tokens are stored in data/oauth/ (git-ignored, owner-only file permissions). They are never
printed, logged, or written to evaluations, events or reports.
"""

from __future__ import annotations

import asyncio
import json
import os
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from vendors_live.config import ROOT

STORE = ROOT / "data" / "oauth"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/callback"
SCOPES = {"airtable": "data.records:read data.records:write schema.bases:read schema.bases:write workspacesAndBases:read"}
MCP_URL = {"airtable": "https://mcp.airtable.com/mcp"}


class FileStorage:
    def __init__(self, vendor: str) -> None:
        self.path: Path = STORE / f"{vendor}.json"

    def _read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _write(self, d: dict) -> None:
        STORE.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    async def get_tokens(self) -> OAuthToken | None:
        t = self._read().get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        d = self._read(); d["tokens"] = tokens.model_dump(mode="json"); self._write(d)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        c = self._read().get("client")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, info: OAuthClientInformationFull) -> None:
        d = self._read(); d["client"] = info.model_dump(mode="json"); self._write(d)


TOKEN_URL = {"airtable": "https://airtable.com/oauth2/v1/token"}
WHOAMI = {"airtable": "https://api.airtable.com/v0/meta/whoami"}


async def ensure_fresh(vendor: str = "airtable", transport=None) -> bool:
    """Make sure the stored login works before it is used; renew it with the refresh login if it was
    rejected. Returns True if it was renewed. The login expires hourly, and the MCP client alone would
    fall into the interactive browser flow instead of renewing (seen live)."""
    import fcntl
    import httpx2
    store = FileStorage(vendor)
    d = store._read()
    tok = (d.get("tokens") or {}).get("access_token")
    kw = {"transport": transport} if transport else {}
    async with httpx2.AsyncClient(timeout=20, **kw) as h:
        if tok and (await h.get(WHOAMI[vendor], headers={"Authorization": f"Bearer {tok}"})).status_code == 200:
            return False
        STORE.mkdir(parents=True, exist_ok=True)
        with open(STORE / f"{vendor}.lock", "w") as lock:          # one renewal at a time across processes
            fcntl.flock(lock, fcntl.LOCK_EX)
            d = store._read()                                       # another process may have just renewed it
            tok2 = (d.get("tokens") or {}).get("access_token")
            if tok2 and tok2 != tok and (await h.get(WHOAMI[vendor], headers={"Authorization": f"Bearer {tok2}"})).status_code == 200:
                return True
            refresh, client_id = (d.get("tokens") or {}).get("refresh_token"), (d.get("client") or {}).get("client_id")
            if not refresh or not client_id:
                raise RuntimeError(f"{vendor} login expired and cannot be renewed: run scripts/airtable_login.py")
            r = await h.post(TOKEN_URL[vendor], data={"grant_type": "refresh_token", "refresh_token": refresh,
                                                      "client_id": client_id})
            if r.status_code != 200:
                raise RuntimeError(f"{vendor} login renewal failed (HTTP {r.status_code}): run scripts/airtable_login.py")
            new = r.json()
            t = dict(d.get("tokens") or {})
            t.update({k: new[k] for k in ("access_token", "token_type", "expires_in", "scope", "refresh_token") if new.get(k)})
            d["tokens"] = t
            store._write(d)
            return True


def has_login(vendor: str) -> bool:
    return bool(FileStorage(vendor)._read().get("tokens"))


async def _wait_for_callback(timeout: float = 600) -> AuthorizationCodeResult:
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = (await reader.readline()).decode(errors="replace")
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        q = parse_qs(urlparse(line.split(" ")[1] if " " in line else "/").query)
        ok = "code" in q
        msg = ("Project Judas: Airtable access approved. You can close this tab." if ok
               else f"Project Judas: Airtable did not return a code ({q.get('error', ['unknown'])[0]}).")
        body = f"<html><body style='font-family:sans-serif;padding:40px'><h2>{msg}</h2></body></html>".encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
                     + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        await writer.drain(); writer.close()
        if not got.done() and ("code" in q or "error" in q):
            got.set_result(q)

    server = await asyncio.start_server(handle, "127.0.0.1", PORT)
    try:
        q = await asyncio.wait_for(got, timeout)
    finally:
        server.close()
    if "code" not in q:
        raise RuntimeError(f"authorization failed: {q.get('error', ['unknown'])[0]}")
    return AuthorizationCodeResult(code=q["code"][0], state=(q.get("state") or [None])[0], iss=(q.get("iss") or [None])[0])


def provider(vendor: str, interactive: bool = False) -> OAuthClientProvider:
    async def open_browser(url: str) -> None:
        if not interactive:
            raise RuntimeError(f"{vendor} OAuth login required: run scripts/airtable_login.py")
        print("Opening Airtable's approval page in your browser...", flush=True)
        webbrowser.open(url)

    async def callback() -> AuthorizationCodeResult:
        if not interactive:
            raise RuntimeError(f"{vendor} OAuth login required: run scripts/airtable_login.py")
        return await _wait_for_callback()

    meta = OAuthClientMetadata(client_name="Project Judas (hackathon buyer agent)", redirect_uris=[REDIRECT],
                               grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                               scope=SCOPES[vendor], token_endpoint_auth_method="none")
    return OAuthClientProvider(MCP_URL[vendor], meta, FileStorage(vendor), redirect_handler=open_browser,
                               callback_handler=callback)
