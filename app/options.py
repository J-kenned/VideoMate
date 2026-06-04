"""yt-dlp option handling: sensible defaults, full exposure, safe by default.

Three layers, lowest precedence first:
  1. server defaults  — our opinionated baseline (format, output location, …)
  2. typed common     — the ``CommonOptions`` model below; the curated 80%
  3. raw passthrough  — an arbitrary yt-dlp opts dict, so *anything* is reachable

The typed layer is expressed as safe CLI tokens *we* generate and feed through
``yt_dlp.parse_options()`` — that reuses yt-dlp's own conversions (filesize units,
ImpersonateTarget, the SponsorBlock/audio postprocessor chains, ``download_ranges``)
instead of us re-implementing them. The raw layer is merged on top, but is first
run through :func:`sanitize_passthrough`, which strips options that would let a
caller run commands, read arbitrary files, or write outside our control. The
operator can lift that fence with ``VIDEOMATE_ALLOW_UNSAFE_OPTIONS=1`` (their box).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yt_dlp
from pydantic import BaseModel, ConfigDict

from .cookies import resolve_cookie_file

# Final-filename template; combined with a forced ``paths.home`` per request.
OUTTMPL = "%(title).200B [%(id)s].%(ext)s"

# opts-dict keys that grant code execution, arbitrary file read, or writes
# outside our control. Key names verified against yt_dlp.parse_options().
# (CLI-only behaviours like --load-info-json / --plugin-dirs produce no opts
# key at all, so they're already unreachable through a dict passthrough.)
DANGEROUS_OPT_KEYS = frozenset({
    # code / external-binary execution
    "exec_cmd", "external_downloader", "external_downloader_args", "ffmpeg_location",
    # arbitrary file read
    "cookiefile", "cookiesfrombrowser", "netrc_cmd", "usenetrc",
    "client_certificate", "client_certificate_key", "client_certificate_password",
    "enable_file_urls",
    # arbitrary write / path control (the server owns output paths)
    "paths", "outtmpl", "cachedir", "download_archive",
    # callables / hooks (defensive — can't arrive over JSON regardless)
    "progress_hooks", "postprocessor_hooks", "post_hooks",
})
# Postprocessors that shell out; allowed PPs (audio, sponsorblock, …) pass through.
DANGEROUS_PP_KEYS = frozenset({"Exec", "ExecAfterDownload"})


def unsafe_allowed() -> bool:
    return os.environ.get("VIDEOMATE_ALLOW_UNSAFE_OPTIONS", "").lower() in ("1", "true", "yes")


class CommonOptions(BaseModel):
    """The curated, always-available options. Anything else → ``raw_options``."""

    model_config = ConfigDict(extra="forbid")  # typos surface instead of silently passing

    # format / quality
    format: Optional[str] = None              # default: bv*+ba/b (or ba/b if audio_only)
    merge_output_format: Optional[str] = None  # default: mp4
    audio_only: bool = False
    audio_format: str = "m4a"
    download_sections: Optional[str] = None    # e.g. "00:01:00-00:02:00" (clip)
    # subtitles
    subtitles: bool = False
    auto_subtitles: bool = False
    sub_langs: Optional[str] = None            # e.g. "en,es"
    embed_subtitles: bool = False
    # embedding
    embed_thumbnail: bool = False
    embed_metadata: bool = False
    embed_chapters: bool = False
    # chapters
    sponsorblock_remove: Optional[str] = None  # e.g. "sponsor,selfpromo"
    # selection / limits
    playlist_items: Optional[str] = None       # e.g. "1-5,8"
    max_filesize: Optional[str] = None         # e.g. "100M"
    match_filter: Optional[str] = None         # e.g. "duration < 600"
    # authentication — cookies come from the server-side store (see COOKIES_DIR),
    # never from the client. cookies=True auto-applies the file matching the URL host.
    cookies: bool = True
    cookies_file: Optional[str] = None         # pick a specific stored file by name
    # network / anti-bot / politeness
    impersonate: Optional[str] = None          # e.g. "chrome" (uses curl_cffi)
    proxy: Optional[str] = None
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    rate_limit: Optional[str] = None           # e.g. "2M"
    retries: Optional[int] = None
    sleep_interval: Optional[float] = None
    # extractor knobs
    extractor_args: Optional[str] = None       # e.g. "youtube:player_client=web"


def _common_tokens(c: CommonOptions) -> list[str]:
    """Translate typed options into CLI tokens we control (``--opt=value`` form so
    a value can never be mistaken for a flag)."""
    fmt = c.format or ("ba/b" if c.audio_only else "bv*+ba/b")
    t = [f"--format={fmt}"]
    if c.audio_only:
        t += ["--extract-audio", f"--audio-format={c.audio_format}"]
    else:
        t.append(f"--merge-output-format={c.merge_output_format or 'mp4'}")
    if c.download_sections:
        section = c.download_sections
        if not section.startswith(("*", "#")):
            section = f"*{section}"
        t.append(f"--download-sections={section}")
    if c.subtitles:
        t.append("--write-subs")
    if c.auto_subtitles:
        t.append("--write-auto-subs")
    if c.sub_langs:
        t.append(f"--sub-langs={c.sub_langs}")
    if c.embed_subtitles:
        t.append("--embed-subs")
    if c.embed_thumbnail:
        t.append("--embed-thumbnail")
    if c.embed_metadata:
        t.append("--embed-metadata")
    if c.embed_chapters:
        t.append("--embed-chapters")
    if c.sponsorblock_remove:
        t.append(f"--sponsorblock-remove={c.sponsorblock_remove}")
    if c.playlist_items:
        t.append(f"--playlist-items={c.playlist_items}")
    if c.max_filesize:
        t.append(f"--max-filesize={c.max_filesize}")
    if c.match_filter:
        t.append(f"--match-filters={c.match_filter}")
    if c.impersonate:
        t.append(f"--impersonate={c.impersonate}")
    if c.proxy:
        t.append(f"--proxy={c.proxy}")
    if c.user_agent:
        t.append(f"--user-agent={c.user_agent}")
    if c.referer:
        t.append(f"--referer={c.referer}")
    if c.rate_limit:
        t.append(f"--limit-rate={c.rate_limit}")
    if c.retries is not None:
        t.append(f"--retries={c.retries}")
    if c.sleep_interval is not None:
        t.append(f"--sleep-interval={c.sleep_interval}")
    if c.extractor_args:
        t.append(f"--extractor-args={c.extractor_args}")
    return t


def sanitize_passthrough(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (clean_opts, stripped_names). No-op when unsafe mode is enabled."""
    if unsafe_allowed():
        return dict(raw), []
    clean: dict[str, Any] = {}
    stripped: list[str] = []
    for key, value in raw.items():
        if key in DANGEROUS_OPT_KEYS:
            stripped.append(key)
            continue
        if key == "postprocessors" and isinstance(value, list):
            kept = [pp for pp in value if pp.get("key") not in DANGEROUS_PP_KEYS]
            if len(kept) != len(value):
                stripped.append("postprocessors[Exec]")
            clean[key] = kept
            continue
        clean[key] = value
    return clean, stripped


def build_opts(
    common: CommonOptions,
    raw: dict[str, Any] | None,
    *,
    url: str,
    dest_dir: Path,
    write_info: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Compose the final yt-dlp opts dict and report any stripped passthrough keys."""
    tokens = ["--ignore-config", "--no-warnings", "--quiet", *_common_tokens(common)]
    base = yt_dlp.parse_options(tokens).ydl_opts

    clean, stripped = sanitize_passthrough(raw or {})
    opts = {**base, **clean}

    # Server-forced output location — wins unless an unsafe-mode caller set its own.
    if not (unsafe_allowed() and "paths" in clean):
        opts["paths"] = {"home": str(dest_dir)}
    if not (unsafe_allowed() and "outtmpl" in clean):
        opts["outtmpl"] = {"default": OUTTMPL}
    opts["noplaylist"] = not bool(common.playlist_items)
    opts["writeinfojson"] = write_info

    # Apply a server-side cookie file (auth) — the server sets this, never the client,
    # so it's safe even though `cookiefile` is on the passthrough deny-list.
    cookie = resolve_cookie_file(url, common.cookies, common.cookies_file)
    if cookie is not None and not (unsafe_allowed() and "cookiefile" in clean):
        opts["cookiefile"] = str(cookie)
    return opts, stripped
