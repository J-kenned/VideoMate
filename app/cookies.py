"""Server-side cookie store: resolution, health inspection, write serialization.

Cookies authenticate downloads (see COOKIES_DIR). yt-dlp refreshes them in place
on every use — it writes rotated session tokens + new expiries back to the file —
so an actively-used, rolling session stays alive on its own. Two things this
module adds on top:

  * expiry inspection — surface "expires in N days" / "expired" so the operator
    knows when a re-export is due (offline, no network).
  * per-file locks — yt-dlp's writeback isn't safe under concurrent use; two
    parallel downloads of the same site would clobber each other's cookie file.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import config

# Warn when the store's longest-lived cookie is within this many days of expiry.
EXPIRY_WARN_DAYS = int(os.environ.get("COOKIE_EXPIRY_WARN_DAYS", "3"))

# One lock per cookie-file path, so writeback can't race across parallel downloads.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(path: str | Path) -> threading.Lock:
    key = str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def available_cookie_domains() -> list[str]:
    """Filenames present in the cookie store (domains we're 'logged in' for)."""
    if not config.COOKIES_DIR.is_dir():
        return []
    return sorted(p.name for p in config.COOKIES_DIR.iterdir() if p.is_file())


def resolve_cookie_file(url: str, enabled: bool, explicit: str | None) -> Path | None:
    """Find the stored cookie file for this URL's host, or an explicitly named one.

    Only ever returns a file located *directly* inside COOKIES_DIR — names are
    reduced to their basename, so a client can't escape the store via traversal.
    """
    if not enabled or not config.COOKIES_DIR.is_dir():
        return None
    base = config.COOKIES_DIR.resolve()

    def _within(name: str) -> Path | None:
        candidate = (base / Path(name).name).resolve()  # .name strips any path parts
        return candidate if candidate.is_file() and candidate.parent == base else None

    if explicit:
        return _within(explicit)

    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    parts = host.split(".")
    # most-specific host first, then registrable-domain suffixes (www.tiktok.com → tiktok.com)
    domains = list(dict.fromkeys(".".join(parts[i:]) for i in range(len(parts) - 1)))
    for dom in domains:
        for fname in (f"{dom}.txt", dom):
            hit = _within(fname)
            if hit:
                return hit
    return None


def _persistent_expiries(path: Path) -> list[int]:
    """Non-zero expiry timestamps from a Netscape cookies.txt (0 = session cookie)."""
    expiries: list[int] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return expiries
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # '#HttpOnly_' lines are real cookies; other '#' lines are comments.
        if stripped.startswith("#") and not stripped.startswith("#HttpOnly_"):
            continue
        fields = stripped.split("\t")
        if len(fields) < 7:
            continue
        try:
            exp = int(float(fields[4]))
        except ValueError:
            continue
        if exp > 0:
            expiries.append(exp)
    return expiries


def inspect_cookie_file(path: Path) -> dict:
    """Offline health read for one cookie file.

    ``expires_at`` is the *latest* persistent cookie — the ceiling on session life,
    which is the right proxy because rolling cookies get refreshed on use.
    ``soonest_expiry`` is exposed too for transparency.
    """
    expiries = _persistent_expiries(path)
    now = int(time.time())
    if not expiries:
        return {
            "status": "session_only",
            "expires_at": None,
            "soonest_expiry": None,
            "expires_in_days": None,
            "persistent_cookies": 0,
        }
    soonest, latest = min(expiries), max(expiries)
    if latest <= now:
        status = "expired"
    elif latest - now <= EXPIRY_WARN_DAYS * 86400:
        status = "expiring_soon"
    else:
        status = "ok"

    def _iso(ts: int) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    return {
        "status": status,
        "expires_at": _iso(latest),
        "soonest_expiry": _iso(soonest),
        "expires_in_days": round((latest - now) / 86400, 1),
        "persistent_cookies": len(expiries),
    }


def cookie_status() -> list[dict]:
    """Health of every file in the store."""
    if not config.COOKIES_DIR.is_dir():
        return []
    return [
        {"file": p.name, **inspect_cookie_file(p)}
        for p in sorted(config.COOKIES_DIR.iterdir())
        if p.is_file()
    ]
