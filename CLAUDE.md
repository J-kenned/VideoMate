# video-mate

A self-hosted server that ingests video URLs and downloads them via yt-dlp. The longer-term vision is to also transcribe audio and index the content via Gemini so the *gist* of each video is stored and searchable — but that AI layer is **deliberately deferred** (see Status).

## Project Goals

**Now (V1):**
- Accept a video URL as input
- Download via yt-dlp
- Run self-hosted in a homelab (Docker)

**Later (deferred — not V1):**
- Transcribe audio (Whisper local, or Gemini audio)
- Index/summarise with Gemini — store the gist (key points, summary, metadata)
- Query/search stored summaries

## Status

**Early stage. V1 scope intentionally narrow: it's a self-hosted yt-dlp downloader.** The transcription + Gemini-indexing + "save the gist somewhere" pipeline is parked — the output format and storage target are *not yet decided* and the user wants to return to them later. Do **not** build the AI layer until that's specced.

## Requirements (decided)

| Decision | Choice |
|---|---|
| **V1 function** | Download videos via yt-dlp (AI/gist layer deferred) |
| **Two modes** | (1) **Host download** — file is fetched + **kept** in the homelab library. (2) **Serve to client** — bytes are **proxied/streamed through the server** to the requester and **not kept**. Both transit the server; the real difference is *retention*. |
| **Serve-to-client = always proxy** | The server never hands out the raw `googlevideo` URL. yt-dlp resolves on the server and we relay the bytes. This is deliberate: raw URLs are **pinned to the server's IP** and **expire in hours**, so they'd 403 for a phone on cellular. Proxying sidesteps both. |
| **Input scope** | **Any yt-dlp-supported site** (no URL whitelist). ~1872 extractors; supporting "any site" is the same code path as YouTube-only. URLs restricted to `http(s)` schemes only (no `file://` etc.). |
| **Deployment** | **Self-hosted Docker, homelab** — runs on the user's own server/NAS, everything local |
| **Stack** | **Python 3.13 + FastAPI**, deps via **uv** (`pyproject.toml` + `uv.lock`, never pip). yt-dlp driven via its **Python API** (`YoutubeDL`), added as a uv dependency — the full option surface is just a dict (see Options). `extract_info` runs in a worker thread so the event loop stays free. |
| **Options exposure** | **Sensible defaults, but everything is reachable.** Three layers: server defaults ← typed `CommonOptions` (the curated ~80%) ← arbitrary `raw_options` passthrough. Typed options become safe CLI tokens fed through `yt_dlp.parse_options()` (reuses yt-dlp's own conversions/postprocessor chains). **Safe by default:** passthrough is filtered against a deny-list (code-exec / file-read / path-write keys, verified against `parse_options`); the operator lifts it with `VIDEOMATE_ALLOW_UNSAFE_OPTIONS=1`. Output paths are always server-forced. |
| **Impersonation** | **curl_cffi** is installed (via `yt-dlp[default,curl-cffi]`) so `options.impersonate` (e.g. `"chrome"`) works — browser-TLS fingerprinting to beat bot-detection. Increasingly needed for YouTube/TikTok. |
| **Authentication** | **Cookies, not user/pass.** Sites like TikTok have no login flow in yt-dlp — auth is a Netscape `cookies.txt`. Operator drops `<domain>.txt` files (e.g. `tiktok.com.txt`) into `COOKIES_DIR` (mounted volume); video-mate auto-applies the file matching the URL host so downloads run "logged in". The **server** sets `cookiefile` (client-supplied cookie paths stay deny-listed); clients only toggle `options.cookies` / pick `options.cookies_file`. Cookie names reduced to basename → no traversal out of the store. |
| **Cookie freshness** | **Refresh-on-use is automatic** (verified): yt-dlp writes rotated session tokens + new expiries back to the cookie file each run, so a regularly-used rolling session stays alive — *provided the cookies volume is mounted read-write*. It **cannot** renew a hard-expired/invalidated session (re-export from a browser). `app/cookies.py` adds: (a) offline expiry inspection surfaced via `GET /cookies` (`ok`/`expiring_soon`/`expired`/`session_only`, warn window `COOKIE_EXPIRY_WARN_DAYS`, default 3); (b) a **per-file lock** around extraction so concurrent same-site downloads don't clobber the writeback. `app/monitor.py` adds a **scheduled monitor** (FastAPI lifespan task, every `COOKIE_CHECK_INTERVAL`s, default 6h; 0 disables) that alerts on *transitions* to `expiring_soon`/`expired` via the log + an optional `COOKIE_ALERT_WEBHOOK` (Discord/Gotify/ntfy-friendly JSON). `POST /cookies/check` runs it on demand. Offline-only — no authenticated probe requests (per-site test URLs + bot-risk). |
| **Library layout** | **Generic per-item folders, not Plex-shaped** (deliberate — keeps the downloader decoupled; a future Plex add-on reorganizes). Download mode writes `<title> [<id>]/` containing the media, `.info.json`, subtitle sidecars, and (if split) a `chapters/NN - <section>.ext` subdir. `/resolve` download now returns `{ dir, files[] }` (each file tagged `video`/`chapter`/`subtitle`/`metadata`), since one request can emit many files. Stream mode is **single-file only** — it disables subtitles/split (they'd fan out) and relays the one media file. Templates: `app/options.FLAT_OUTTMPL` (stream/info) vs `ITEM_OUTTMPL` (download). |
| **Subtitles & chapters** | **Subtitles on by default** (`options.subtitles`): writes `.srt` sidecars, manual preferred + auto-captions as fallback, langs default `en` (override `sub_langs`, e.g. `all`); `embed_subtitles` to mux instead. TikTok/IG usually have none; YouTube has auto-captions. **`options.split_chapters`** writes one clip per chapter into `chapters/` *and* keeps the full file (~2× storage). |
| **Interfaces** | **REST API** (the core backbone) + **Web UI** + **chat bot** (Telegram/Discord) + **CLI / share-sheet**. API first; the others are clients on top of it. |

## Requirements (still open — revisit before building the AI layer)

- **Output / "the gist":** what's produced and where it's stored (markdown files vs database vs vector store vs push-to-Notion). *Undecided.*
- **Transcription engine:** local Whisper vs Gemini native audio vs Whisper API vs reuse existing captions. *Undecided.*
- **Search/retrieval:** whether semantic search across summaries is needed (→ would imply a vector store). *Undecided.*
- **Playlists / channels / subscriptions:** deferred to a **V2 async job model** (submit → job id → poll). A playlist is multi-output + long-running, so it can't ride the synchronous request path; single-video chapter-split already ships sync. Playlist layout would nest: `<playlist>/<title>/…` (folder-of-folders when split is on). Channels/auto-watch still unspecced.
- **Plex add-on (future):** a *separate* layer over the generic library — hardlink/rename into Plex conventions (`Show/Season 01/… SxxEyy`, `.nfo`), mapping **chapters→episodes** and **playlist→show/season**. Not built; the generic layout exists so it can be.
- **Retention policy for *kept* media:** host-download mode keeps files, but eviction/cleanup/quota is undecided.

## Architecture Notes (V1)

- **REST API is the backbone.** Web UI, chat bot, and CLI/share-sheet are all clients that POST a URL to the API. Build the API first.
- **Self-hosted Docker** — keeps yt-dlp on a residential IP (cloud IPs get rate-limited/blocked more), and keeps everything private. Pairs naturally with local Whisper *if/when* transcription is added.
- **Python 3.13 + FastAPI**, deps managed with **uv**. yt-dlp driven via the **Python API** (`YoutubeDL`); see Options for why and how the option surface is composed. Layout: `app/main.py` (routes), `app/ytdlp.py` (API wrapper), `app/options.py` (option layering + safety), `app/config.py` (env settings).
- **Endpoints:** `POST /resolve { url, mode, options, raw_options }` (`download` → keep + return `{ dir, files[], metadata, dropped_options }`; `stream` → single-file temp download → `FileResponse` → delete, dropped opts in `X-Dropped-Options` header). Plus `GET /health`, `GET /info?url=` (metadata preview), `GET /options` (typed schema + `blocked_keys`), `GET /cookies` (per-domain expiry health), `POST /cookies/check` (run the health check + alert now).
- **Security:** the option deny-list is the main attack surface. `app/options.DANGEROUS_OPT_KEYS` is verified against `yt_dlp.parse_options()` — re-verify it when bumping yt-dlp, since new options could add exec/file-read paths. `extra="forbid"` on `CommonOptions` makes typos 422 rather than silently pass.

### Findings from yt-dlp exploration (these set V1)

- **ffmpeg is required** for full quality — high-res YouTube is split DASH (separate video + audio); ffmpeg merges to a single mp4. Present in the image.
- **YouTube needs a JS runtime (deno):** extraction now solves JS challenges via `[jsc:deno]`. **The Docker image must bundle deno** or YouTube extraction breaks.
- Raw resolved URLs carry `ip=` (server-pinned) and `expire=` (hours) — the reason serve-to-client proxies rather than redirects.
- Metadata comes back in one extract call (`title`, `uploader`, `duration`, `upload_date`, `thumbnail`, `view_count`, `extractor`, …) — cheap to return to clients now, and exactly what the future AI layer should persist.
- **Providers:** ~1871 site-specific extractors **+ a `generic` extractor** that sniffs arbitrary pages — coverage is effectively "anything with a video," not just the big names.
- **Blobs:** yt-dlp **cannot** fetch a browser `blob:` URL (no protocol handler — it's an in-memory MediaSource object). But the HLS (`m3u8`) / DASH (`mpd`) streams *behind* a blob are fully supported — point yt-dlp at the **page URL** (generic extractor finds the manifest) or the **`.m3u8`/`.mpd` URL** from DevTools. Worth surfacing in the UI.
- **292 yt-dlp options** across 16 groups — most are safe quality/behaviour knobs; a handful (exec, external-downloader, ffmpeg-location, cookies/netrc/cert file-reads, paths/outtmpl) are the dangerous set the deny-list guards.

- Likely **Python** stack remains the long-term fit (future Whisper/Gemini SDKs).

## Notes for when the AI layer is revived

- Store enough metadata (title, URL, duration, date, source) alongside any future gist so videos are findable later.
- Transcripts can be long — chunk before sending to Gemini if needed.
- Gemini model: default `gemini-2.5-pro`; use `gemini-2.5-flash` for latency-sensitive/bulk steps.

## Notes for Working in This Repo

- This repo's `CLAUDE.md` is force-tracked (the user's global gitignore excludes `CLAUDE.md`). Use `git add -f` or add a `!CLAUDE.md` negation in a local `.gitignore`.
- **Deps are managed with `uv`, never pip.** `uv add <pkg>` to add, `uv sync` to install, `uv run …` to execute. e.g. dev server: `uv run uvicorn app.main:app --reload`.
- Possible future tie-in: a **shortcutey** share-sheet/shortcut could fire URLs at video-mate's REST API.
