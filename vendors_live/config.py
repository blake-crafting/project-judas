"""Local configuration for REAL vendor integrations (Airtable, monday.com).

Values come from the git-ignored `.env` at the repo root or from the process environment.
They are never printed, logged, or written into evaluations, events or reports.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
NAMES = {"airtable": "AIRTABLE_TOKEN", "monday": "MONDAY_TOKEN"}


def _load_file() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def credential(vendor: str) -> str | None:
    """The configured credential for a vendor, or None. Environment wins over .env."""
    name = NAMES[vendor]
    return os.environ.get(name) or _load_file().get(name) or None


def status() -> dict[str, bool]:
    """Which vendors are configured (booleans only): a complete static credential or a stored OAuth login."""
    from vendors_live import oauth
    from vendors_live.mcp_live import _usable_token
    return {v: bool(_usable_token(v)) or (v in oauth.MCP_URL and oauth.has_login(v)) for v in NAMES}
