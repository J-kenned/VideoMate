"""video-mate REST API — the backbone all other clients sit on top of.

Two modes, both run yt-dlp on the server; the difference is retention:
  - download: fetch + keep in the homelab library, return metadata + path.
  - stream:   fetch to a throwaway dir, relay the bytes to the caller, delete.

stream proxies deliberately — raw resolved URLs are IP-pinned to the server and
expire within hours, so handing them to a phone on cellular would 403.

Every request can carry yt-dlp options: a curated typed set (``options``) plus an
arbitrary passthrough dict (``raw_options``). Passthrough is safety-filtered — see
:mod:`app.options`.
"""

from __future__ import annotations

import asyncio
import mimetypes
import shutil
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl
from starlette.background import BackgroundTask

from . import config, monitor, ytdlp
from .cookies import available_cookie_domains, cookie_status
from .options import CommonOptions, DANGEROUS_OPT_KEYS, unsafe_allowed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the cookie-health monitor in the background while the app is up."""
    task = None
    if config.COOKIE_CHECK_INTERVAL > 0:
        task = asyncio.create_task(monitor.run_monitor())
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="video-mate", version="0.1.0", lifespan=lifespan)


class Mode(str, Enum):
    download = "download"  # fetch + keep
    stream = "stream"      # proxy through to the client, don't keep


class ResolveRequest(BaseModel):
    url: HttpUrl
    mode: Mode = Mode.download
    options: CommonOptions = Field(default_factory=CommonOptions)
    raw_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary yt-dlp opts-dict (full surface); safety-filtered unless unsafe mode is on.",
    )


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "yt_dlp": ytdlp.version(),
        "ffmpeg": ytdlp.has_ffmpeg(),
        "unsafe_options": unsafe_allowed(),
        "cookie_domains": available_cookie_domains(),
    }


@app.get("/cookies")
async def cookies() -> dict:
    """Per-domain cookie health (names + expiry status, never contents)."""
    return {
        "cookies_dir": str(config.COOKIES_DIR),
        "cookies": cookie_status(),
    }


@app.post("/cookies/check")
async def cookies_check() -> dict:
    """Run the health check now and fire alerts for anything newly degraded."""
    alerts = await monitor.check_once()
    return {"checked": len(available_cookie_domains()), "alerts": alerts}


@app.get("/options")
async def options() -> dict:
    """Discoverability: the typed schema, plus what passthrough will reject."""
    return {
        "common": CommonOptions.model_json_schema(),
        "passthrough": "any yt-dlp opts-dict key via raw_options",
        "blocked_keys": sorted(DANGEROUS_OPT_KEYS),
        "unsafe_enabled": unsafe_allowed(),
    }


@app.get("/info")
async def info(url: str = Query(..., description="Video URL")) -> dict:
    """Metadata-only preview — lets a client decide before downloading."""
    try:
        ytdlp.validate_url(url)
        return await ytdlp.get_info(url, CommonOptions(), {})
    except ytdlp.YtdlpError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/resolve")
async def resolve(req: ResolveRequest):
    url = str(req.url)
    try:
        ytdlp.validate_url(url)
    except ytdlp.YtdlpError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if req.mode is Mode.download:
        try:
            path, meta, dropped = await ytdlp.download(
                url, req.options, req.raw_options, config.DOWNLOAD_DIR, write_info=True
            )
        except ytdlp.YtdlpError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {
            "mode": "download",
            "file": path.name,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "dropped_options": dropped,
            "metadata": meta,
        }

    # stream: download to a unique throwaway dir, relay, delete after sending.
    workdir = config.TMP_DIR / uuid.uuid4().hex
    try:
        path, _, dropped = await ytdlp.download(
            url, req.options, req.raw_options, workdir, write_info=False
        )
    except ytdlp.YtdlpError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=502, detail=str(exc))

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"X-Dropped-Options": ",".join(dropped)} if dropped else None
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers=headers,
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )
