"""Runtime configuration, all overridable via environment variables."""

from __future__ import annotations

import os
from pathlib import Path

# Where host-download mode keeps files (the homelab library).
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "downloads")).expanduser().resolve()

# Scratch space for stream mode: download here, relay, then delete.
TMP_DIR = Path(os.environ.get("TMP_DIR", "/tmp/video-mate")).expanduser().resolve()

# Operator-provisioned cookie store: Netscape cookies.txt files named by domain
# (e.g. "tiktok.com.txt"), auto-applied by URL host so downloads run "logged in".
# Read-only from the server's POV; clients never supply cookie paths.
COOKIES_DIR = Path(os.environ.get("COOKIES_DIR", "cookies")).expanduser().resolve()

# Background cookie-health monitor: how often (seconds) to check expiry and alert.
# Set to 0 to disable the scheduled check (on-demand POST /cookies/check still works).
COOKIE_CHECK_INTERVAL = int(os.environ.get("COOKIE_CHECK_INTERVAL", str(6 * 3600)))

# Optional webhook (Discord/Gotify/ntfy/etc.) POSTed when a cookie needs attention.
COOKIE_ALERT_WEBHOOK = os.environ.get("COOKIE_ALERT_WEBHOOK") or None
