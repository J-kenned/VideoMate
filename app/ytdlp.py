"""yt-dlp execution via its Python API (``YoutubeDL``).

We drive the library in-process (not as a subprocess) so the full option surface
is just a dict — see :mod:`app.options` for how defaults, the typed common layer,
and sanitized passthrough are composed. extract_info is blocking, so it's run in
a worker thread to keep the event loop free.
"""

from __future__ import annotations

import asyncio
import shutil
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from yt_dlp.version import __version__ as _YTDLP_VERSION

from .cookies import lock_for
from .options import FLAT_OUTTMPL, CommonOptions, build_opts

# Subset of yt-dlp's metadata surfaced to clients — also what the future AI/gist
# layer should persist (title, source, duration, …).
_INFO_FIELDS = (
    "id", "title", "uploader", "channel", "duration", "upload_date",
    "thumbnail", "webpage_url", "extractor", "ext", "view_count", "is_live",
)


class YtdlpError(Exception):
    """yt-dlp failed, or the request was rejected before invoking it."""


def validate_url(url: str) -> None:
    """Reject anything that isn't http(s) — no file://, no local paths."""
    if urlparse(url).scheme.lower() not in ("http", "https"):
        raise YtdlpError("only http(s) URLs are accepted")


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def version() -> str:
    return _YTDLP_VERSION


def _curate(info: dict) -> dict:
    return {k: info.get(k) for k in _INFO_FIELDS}


def _extract(opts: dict[str, Any], url: str, *, download: bool) -> tuple[Path | None, dict]:
    """Blocking call — run inside a worker thread.

    When a cookie file is in play, serialize on its lock: yt-dlp writes refreshed
    cookies back to that file, and concurrent runs would clobber each other.
    """
    cookiefile = opts.get("cookiefile")
    guard = lock_for(cookiefile) if cookiefile else nullcontext()
    try:
        with guard, YoutubeDL(opts) as ydl:  # type: ignore[arg-type]  # opts is a plain dict
            info = ydl.extract_info(url, download=download)
            if info is None:
                raise YtdlpError("yt-dlp returned no information")
            path = None
            if download:
                requested = info.get("requested_downloads") or []
                if requested and requested[0].get("filepath"):
                    path = Path(requested[0]["filepath"])
            return path, ydl.sanitize_info(info)  # type: ignore[return-value]
    except DownloadError as exc:
        raise YtdlpError(str(exc)) from exc
    except YtdlpError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any extractor failure cleanly
        raise YtdlpError(f"{type(exc).__name__}: {exc}") from exc


async def get_info(url: str, common: CommonOptions, raw: dict[str, Any] | None) -> dict:
    """Fetch metadata only (no download)."""
    opts, _ = build_opts(
        common, raw, url=url, dest_dir=Path("."), write_info=False, outtmpl=FLAT_OUTTMPL
    )
    _, info = await asyncio.to_thread(_extract, opts, url, download=False)
    return _curate(info)


async def download(
    url: str,
    common: CommonOptions,
    raw: dict[str, Any] | None,
    dest_dir: Path,
    *,
    write_info: bool,
    outtmpl: dict[str, str],
) -> tuple[Path, dict, list[str]]:
    """Download into ``dest_dir``. Returns (primary_file, metadata, dropped_options).

    A download may emit more than the primary file (subtitle sidecars, per-chapter
    clips, .info.json) — see :func:`list_outputs` to enumerate them.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts, stripped = build_opts(
        common, raw, url=url, dest_dir=dest_dir, write_info=write_info, outtmpl=outtmpl
    )
    path, info = await asyncio.to_thread(_extract, opts, url, download=True)

    if path is None or not path.is_file():
        path = _newest_media(dest_dir)
    if path is None:
        raise YtdlpError("download produced no file")
    return path, _curate(info), stripped


# Extensions used to classify the files a download leaves behind.
_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus", ".aac", ".flac", ".ogg"}
_SUB_EXTS = {".srt", ".vtt", ".ass"}


def list_outputs(item_dir: Path) -> list[dict]:
    """Enumerate everything a download produced under its per-item folder."""
    outputs: list[dict] = []
    for p in sorted(item_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(item_dir)
        ext = p.suffix.lower()
        if p.name.endswith(".info.json"):
            kind = "metadata"
        elif "chapters" in rel.parts and ext in _VIDEO_EXTS:
            kind = "chapter"
        elif ext in _VIDEO_EXTS:
            kind = "video"
        elif ext in _SUB_EXTS:
            kind = "subtitle"
        else:
            kind = "other"
        outputs.append({"name": str(rel), "kind": kind, "size_bytes": p.stat().st_size})
    return outputs


def _newest_media(dest_dir: Path) -> Path | None:
    """Fallback when the API didn't report a path: newest non-sidecar media file."""
    files = [
        f for f in dest_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
    ]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None
