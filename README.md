# video-mate

Self-hosted yt-dlp video downloader with a REST API. Runs in a homelab; the API
is the backbone that future clients (web UI, chat bot, CLI/share-sheet) sit on.

> V1 is **just the downloader**. The transcription + Gemini "gist" layer is
> deliberately deferred — see [CLAUDE.md](CLAUDE.md).

## Two modes

| Mode       | What it does                                            | Media kept? |
|------------|---------------------------------------------------------|-------------|
| `download` | Fetches the video onto the server (the homelab library) | **Yes**     |
| `stream`   | Proxies the bytes through the server to the caller      | No          |

Both run yt-dlp server-side. `stream` proxies rather than handing out the raw
URL because resolved URLs are pinned to the server's IP and expire within hours.

## Requirements

- [uv](https://docs.astral.sh/uv/) — dependency management (not pip)
- `yt-dlp`, `ffmpeg`, and a JS runtime (`deno`) on `PATH` — all baked into the
  Docker image; install them yourself for bare-metal dev.

## Run locally

```sh
uv sync
uv run uvicorn app.main:app --reload
```

## Run in Docker

```sh
docker build -t video-mate .
docker run -p 8000:8000 -v "$PWD/downloads:/data/downloads" video-mate
```

## API

| Method | Path       | Purpose                                             |
|--------|------------|-----------------------------------------------------|
| GET    | `/health`  | Liveness + yt-dlp version + ffmpeg presence         |
| GET    | `/options` | Typed-option schema + the passthrough `blocked_keys` |
| GET    | `/info`    | Metadata-only preview (`?url=`)                     |
| POST   | `/resolve` | `{ url, mode, options, raw_options }`               |
| GET    | `/cookies` | Per-domain cookie expiry health                     |
| POST   | `/cookies/check` | Run the cookie health check + fire alerts now |

```sh
# Metadata only
curl 'localhost:8000/info?url=https://youtu.be/jNQXAC9IVRw'

# Download to the server library
curl -X POST localhost:8000/resolve -H 'content-type: application/json' \
  -d '{"url":"https://youtu.be/jNQXAC9IVRw","mode":"download"}'

# Stream the file back to the client
curl -X POST localhost:8000/resolve -H 'content-type: application/json' \
  -d '{"url":"https://youtu.be/jNQXAC9IVRw","mode":"stream"}' -o video.mp4

# Audio only, clipped to a time range (typed options)
curl -X POST localhost:8000/resolve -H 'content-type: application/json' \
  -d '{"url":"https://youtu.be/jNQXAC9IVRw","options":{"audio_only":true,"download_sections":"00:01:00-00:02:00"}}'
```

## Options

Sensible defaults, but every yt-dlp option is reachable:

- **`options`** — curated, typed, validated fields (`format`, `audio_only`,
  `download_sections`, `subtitles`, `sponsorblock_remove`, `impersonate`,
  `proxy`, `playlist_items`, …). See `GET /options` for the full schema.
- **`raw_options`** — an arbitrary yt-dlp opts-dict for anything not typed.

`raw_options` is **safety-filtered**: keys that enable command execution,
arbitrary file reads, or writes outside the library are stripped (and reported
in `dropped_options` / the `X-Dropped-Options` header). The operator can disable
the filter on their own box with `VIDEOMATE_ALLOW_UNSAFE_OPTIONS=1`. Output
paths are always server-controlled regardless.

> **Blobs:** yt-dlp can't fetch a browser `blob:` URL directly, but it handles
> the HLS/DASH stream behind it — pass the **page URL**, or the `.m3u8`/`.mpd`
> manifest URL from your browser's DevTools → Network tab.

## Authentication (download videos only your account can see)

Sites like TikTok have **no username/password login** in yt-dlp — you
authenticate with **cookies** from a browser where you're already logged in.

1. Log into the site (e.g. TikTok) in your browser.
2. Export cookies with a *cookies.txt* extension (e.g. "Get cookies.txt LOCALLY")
   in **Netscape format**.
3. Drop the file into the cookie store, named by domain — `tiktok.com.txt`:
   - local dev: `./cookies/tiktok.com.txt`
   - Docker: the mounted `/data/cookies` volume
4. Done. video-mate auto-applies the matching cookie file by URL host, so
   requests to that site run logged-in. `GET /cookies` shows which domains are set.

Per-request controls: `options.cookies: false` to force anonymous, or
`options.cookies_file: "name.txt"` to pin a specific stored file. For sites with
aggressive bot-detection, pair cookies with `options.impersonate: "chrome"`.

> Cookies are session secrets — the `cookies/` dir is git-ignored. The server only
> reads from its own cookie store; clients can never point it at an arbitrary path.

### Keeping cookies fresh

- **Refresh-on-use is automatic.** Each time a cookie file is used, yt-dlp writes
  the site's rotated session tokens and new expiry dates back to it. Sites that
  use rolling sessions stay logged-in on their own as long as you keep using them
  — **as long as the `cookies` volume is mounted read-write** (it is by default).
- **Check health anytime** with `GET /cookies` — per-domain `status`
  (`ok` / `expiring_soon` / `expired` / `session_only`) and `expires_in_days`.
  Tune the warning window with `COOKIE_EXPIRY_WARN_DAYS` (default `3`).
- **Get alerted before they lapse.** A background monitor checks expiry every
  `COOKIE_CHECK_INTERVAL` seconds (default 6h; `0` disables) and notifies you when
  a cookie goes `expiring_soon` or `expired` — via the log and, if set, a
  `COOKIE_ALERT_WEBHOOK` (JSON with a `content` field, so Discord/Gotify/ntfy work
  out of the box). Alerts fire once per transition, not on a loop. Force a check
  anytime with `POST /cookies/check`.
- **What it can't do:** renew a *hard-expired or invalidated* session (logout
  elsewhere, password change, security challenge). No tool can — re-export from a
  logged-in browser when `/cookies` shows `expired`, or a download fails auth.
- Concurrent downloads of the same site are serialized on a per-file lock so the
  cookie writeback can't be clobbered.
