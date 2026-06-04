"""Scheduled cookie-health monitor + operator notification.

Runs the offline expiry check (see :mod:`app.cookies`) on a cadence and alerts the
operator — via the app log and an optional webhook — when a cookie is expiring soon
or already expired, so it can be re-exported *before* downloads start failing auth.

Alerts fire on *transitions* only (first time a file goes `expiring_soon`, again if
it escalates to `expired`), never repeatedly for the same known-bad state.

Deliberately offline: it inspects expiry timestamps, it does not make
authenticated probe requests (those need a per-site test URL and risk looking like
bot traffic). It catches the common case — sessions lapsing on schedule.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from . import config
from .cookies import cookie_status

log = logging.getLogger("video-mate.monitor")

# Statuses worth bothering the operator about.
ALERT_STATUSES = frozenset({"expiring_soon", "expired"})

# Files we've already alerted on, mapped to the status we alerted at. Module-global
# is fine for a single-process app; transitions are computed against it.
_alerted: dict[str, str] = {}


def evaluate(
    statuses: list[dict], alerted: dict[str, str]
) -> tuple[list[dict], dict[str, str]]:
    """Pure: given current cookie statuses and what we've already alerted on,
    return (new_alerts, updated_alerted_state). Healthy files clear prior alerts."""
    updated = dict(alerted)
    alerts: list[dict] = []
    for cookie in statuses:
        name, status = cookie["file"], cookie["status"]
        if status in ALERT_STATUSES:
            if updated.get(name) != status:  # new, or escalated soon→expired
                alerts.append(cookie)
                updated[name] = status
        else:
            updated.pop(name, None)  # recovered (re-exported) → re-arm
    return alerts, updated


def _format(alerts: list[dict]) -> str:
    lines = [
        f"  • {a['file']}: {a['status']}"
        + (f" (expires {a['expires_at']}, {a['expires_in_days']}d)" if a["expires_at"] else "")
        for a in alerts
    ]
    return "video-mate: cookies need attention —\n" + "\n".join(lines)


def _post_webhook(url: str, message: str, alerts: list[dict]) -> None:
    # `content` satisfies Discord; `message`/`text` cover Gotify/ntfy/Slack-ish; the
    # structured `cookies` array is there for anything that wants to parse it.
    body = json.dumps({
        "service": "video-mate",
        "event": "cookie_alert",
        "content": message,
        "message": message,
        "text": message,
        "cookies": alerts,
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001 — a failed alert must not crash the monitor
        log.warning("cookie alert webhook failed: %s", exc)


async def notify(alerts: list[dict]) -> None:
    message = _format(alerts)
    log.warning(message)
    if config.COOKIE_ALERT_WEBHOOK:
        await asyncio.to_thread(_post_webhook, config.COOKIE_ALERT_WEBHOOK, message, alerts)


async def check_once() -> list[dict]:
    """Run one health check, fire alerts for any new problems, return them."""
    global _alerted
    alerts, _alerted = evaluate(cookie_status(), _alerted)
    if alerts:
        await notify(alerts)
    return alerts


async def run_monitor() -> None:
    """Background loop — checks immediately on startup, then every interval."""
    interval = config.COOKIE_CHECK_INTERVAL
    log.info("cookie monitor started (interval=%ss)", interval)
    while True:
        try:
            await check_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — keep the loop alive on transient errors
            log.warning("cookie check failed: %s", exc)
        await asyncio.sleep(interval)
