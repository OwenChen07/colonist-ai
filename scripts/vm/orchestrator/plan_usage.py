"""How much of the Max plan is used: the percentages Claude Code's /usage shows.

Reads https://api.anthropic.com/api/oauth/usage with the OAuth token Claude
Code keeps in ~/.claude/.credentials.json. The endpoint is undocumented, so
every failure (no file, expired token, HTTP error, a changed format) gives
None and the callers fall back to the dollar budget. The token is never
printed or logged.

.venv/bin/python scripts/vm/orchestrator/plan_usage.py   # prints "5 h: 42%, week: 61%"
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_S = 60
_cache = {"at": None, "value": None}


def credentials_path():
    default = Path.home() / ".claude" / ".credentials.json"
    return Path(os.environ.get("ORCHESTRATOR_CLAUDE_CREDENTIALS", default))


def token(path=None, now=None):
    """The OAuth access token, or None if missing or expired."""
    try:
        oauth = json.loads(Path(path or credentials_path()).read_text())[
            "claudeAiOauth"
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    expires = oauth.get("expiresAt")  # milliseconds
    now = time.time() if now is None else now
    if expires and float(expires) / 1000 <= now:
        return None
    return oauth.get("accessToken") or None


def parse(data):
    """{'five_hour': %, 'seven_day': %, '<window>_resets': iso} from the
    endpoint's JSON, or None if either percentage is missing."""
    out = {}
    try:
        for window in ("five_hour", "seven_day"):
            part = data.get(window) or {}
            out[window] = float(part["utilization"])
            out[f"{window}_resets"] = part.get("resets_at")
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return out


def fetch(timeout=5):
    secret = token()
    if not secret:
        return None
    request = urllib.request.Request(
        URL,
        headers={
            "Authorization": f"Bearer {secret}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            "User-Agent": "colonist-ai-orchestrator",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse(json.load(response))
    except Exception:  # HTTP, network, JSON: all mean "unknown"
        return None


def usage(clock=time.monotonic):
    """fetch(), cached CACHE_S seconds (failures too, so a dead endpoint
    isn't asked every supervise tick)."""
    at = clock()
    if _cache["at"] is None or at - _cache["at"] >= CACHE_S:
        _cache["at"], _cache["value"] = at, fetch()
    return _cache["value"]


def line(plan):
    """'5 h: 42%, week: 61%', or '' when unknown."""
    if not plan:
        return ""
    return f"5 h: {plan['five_hour']:.0f}%, week: {plan['seven_day']:.0f}%"


def main():
    print(line(usage()) or "plan usage unknown (endpoint or token unavailable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
