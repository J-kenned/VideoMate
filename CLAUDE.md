# video-mate

A server that ingests video URLs, downloads them via yt-dlp, transcribes the audio, and indexes the content via Gemini so the gist of each video is stored and searchable.

## Project Goals

- Accept a video URL as input
- Download via yt-dlp
- Transcribe audio (likely Whisper or Gemini audio)
- Index/summarise with Gemini — store the gist (key points, summary, metadata)
- Query stored summaries later

## Status

Early stage — architecture not yet decided. Focus is on the pipeline: URL in → summary/transcript stored.

## Tech Decisions (TBD)

- Language/framework: not decided
- Storage: not decided (could be sqlite, postgres, or a vector store for search)
- Transcription: Whisper (local) or Gemini (API)
- Gemini model: use `gemini-2.5-pro` unless a task is latency-sensitive (then `gemini-2.5-flash`)

## Key Dependencies

- `yt-dlp` — video download
- Gemini API — indexing and summarisation
- Transcription layer TBD

## Notes

- yt-dlp should be called as a subprocess or via its Python API
- Store enough metadata (title, URL, duration, date) alongside the gist so videos are findable later
- Transcripts can be long — chunk before sending to Gemini if needed
