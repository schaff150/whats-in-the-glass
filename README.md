# What’s in the Glass?

A production-shaped, LAN-local spirits guide with the **Leather Ledger** interface. Enter a bottle name first or upload a label photograph as the secondary path, receive an honest deterministic demo briefing, optionally save a 1–5 rating and one likes/dislikes note, and revisit lightweight recent history.

## Current milestone

This foundation includes:

- a FastAPI application and multipart JSON API;
- SQLite persistence for analyses and optional feedback;
- a no-build mobile PWA with a static-shell service worker;
- a replaceable `AnalysisService` boundary;
- Docker and Compose deployment definitions for port 8787.

The current service has **no real model, web search, image recognition, or TTS**. It returns the approved Glenrothes sample for recognized Glenrothes text, labels unknown names as demonstrations, and reports photo-only submissions as “Bottle photograph — identification pending.” The disabled audio card is explicit: Hamish narration is the next integration. It does not simulate playback.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose only if using the container path

No credentials or provider API keys are required for this milestone.

## Local setup

From the repository root:

```bash
uv sync
uv run pytest -q
```

Run the service on the production port:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8787
```

Open <http://localhost:8787/>. Check health at <http://localhost:8787/api/health>.

By default, SQLite is created at `data/witg.sqlite3`. Override the data directory in Git Bash or another POSIX-style shell:

```bash
WITG_DATA_DIR=C:/path/to/witg-data uv run uvicorn app.main:app --host 0.0.0.0 --port 8787
```

For LAN use, allow TCP port 8787 only on trusted/private networks and browse to `http://<host-lan-ip>:8787/`. This application does not provide authentication or TLS; do not expose it directly to the public internet.

## API summary

- `GET /api/health` — service status
- `POST /api/analyses` — multipart fields `drinker`, optional `guest_name`, optional `drink_name`, optional `photo`
- `POST /api/analyses/{id}/feedback` — JSON `rating` and `notes`
- `GET /api/history` — newest analyses first, joined with feedback

An analysis requires text or a photograph. Valid photographs are JPEG, PNG, or WebP and no larger than 5 MiB. Drinkers are `Me`, `Wife`, or `Other`; a guest name is retained only for `Other`. Ratings, when supplied, must be 1–5.

## Docker Compose

```bash
docker compose up --build
```

The Compose service maps host port 8787, sets `WITG_DATA_DIR=/data`, and persists SQLite in the named `witg-data` volume. Its health check uses Python’s standard library, so the image does not need curl.

Stop the service without deleting data:

```bash
docker compose down
```

Delete the named data volume only when intentional:

```bash
docker compose down --volumes
```

**Verification limitation:** Docker is not installed on Dadbase, so the image was not built or started on that workstation. The Dockerfile and Compose contracts are covered by automated static tests; build verification must occur on a Docker-capable host.

## PWA behavior

The manifest, icons, Apple touch icon, favicon, and root-scoped service worker are served by FastAPI. The service worker pre-caches only the static application shell. API requests remain network-first and are never placed in the shell cache, so history and feedback are not presented as fresh when the server is unavailable.

## Next integration seam

Replace `DemoAnalysisService` in `app/services/analysis.py` with an `AnalysisService` implementation that:

1. identifies text and photographs with a configurable multimodal model;
2. performs grounded web search and returns cited sources;
3. persists the resulting briefing through the existing database boundary;
4. generates and caches ElevenLabs Hamish narration only after the researched text is final.

Provider names, credentials, model selection, and outbound-call policy remain intentionally unselected.

## Operator checks

```bash
uv run pytest -q
python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8787/api/health').read().decode())"
```

A healthy response is:

```json
{"status":"healthy","service":"whats-in-the-glass"}
```
