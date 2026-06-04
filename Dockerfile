# uv base image ships uv + CPython 3.13.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# System deps:
#  - ffmpeg: merges split video+audio streams into one mp4 / extracts audio.
#  - deno:   yt-dlp needs a JS runtime to solve YouTube's JS challenges.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app

# Install app deps first for layer caching; --frozen enforces the committed lock.
# yt-dlp is a project dependency, so it lands in the venv here (no separate install).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

# Host-download library + cookie store live on volumes so they survive rebuilds.
# Drop Netscape cookies.txt files (e.g. tiktok.com.txt) into /data/cookies to auth.
ENV DOWNLOAD_DIR=/data/downloads \
    TMP_DIR=/tmp/video-mate \
    COOKIES_DIR=/data/cookies
VOLUME ["/data/downloads", "/data/cookies"]

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
