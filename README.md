# What’s in the Glass?

A LAN-local spirits guide with the mobile **Leather Ledger** interface. It can run as an honest deterministic demo, or use an administrator-configured OpenAI-compatible chat-completions model and ElevenLabs voice from the in-app **Connections** view.

## Current milestone

- FastAPI, SQLite, and a no-build mobile PWA on port 8787.
- Text and JPEG/PNG/WebP bottle analysis.
- OpenAI-compatible `GET /models` discovery, manual model IDs, connection testing, and strict JSON chat-completions analysis.
- ElevenLabs voice discovery, connection testing, preview, and on-demand cached narration for saved analyses.
- Write-only provider keys encrypted with AES-256-GCM under an externally mounted 32-byte master key.
- A signed, expiring administrator session protecting every Connections/settings route.
- Demo fallback when no real model is configured. Generic OpenAI-compatible APIs do not standardize grounded search, so configured-model analyses intentionally return `sources: []` rather than fabricating citations.

ChatGPT subscription/account login is **not** supported. ChatGPT OAuth needs a separate account adapter and is deferred.

## Security boundary

**HTTPS is required before entering real credentials.** The application does not terminate TLS. Put it behind a locally trusted HTTPS reverse proxy on the private LAN. Do not expose it directly to the public internet.

By default, admin login and credential writes fail closed over HTTP with `https_required`. `WITG_ALLOW_INSECURE_CREDENTIALS=true` is an explicit temporary override for a fully trusted LAN during setup; it is not equivalent to TLS and must not be used for public or untrusted networks. Credentials still traverse that HTTP connection in cleartext.

For Tailscale Serve, trust only the exact host-side proxy source. On DadServer’s verified default Docker bridge, Tailscale Serve forwarding from the host enters the container from `172.17.0.1`. Set both Uvicorn’s `FORWARDED_ALLOW_IPS` and the app’s `WITG_TRUSTED_PROXY_IPS` to that exact IP (or an explicitly reviewed CIDR), never `*`. The Compose default is operator-overridable:

```bash
WITG_TRUSTED_PROXY_IPS=172.17.0.1 docker compose up --build
```

Only that trusted source may supply `X-Forwarded-Proto: https` and `X-Forwarded-For`. Direct LAN clients retain their `192.168.0.x` identity and cannot spoof forwarded HTTPS. Login throttling therefore uses the safely forwarded Tailscale client address when supplied; if a trusted proxy omits `X-Forwarded-For`, requests intentionally share an `unknown` proxy bucket. Final Tailscale Serve → host port → container verification is a deployment step and is not claimed by unit tests.

Connections access requires two read-only files mounted outside the SQLite data volume:

- `WITG_SECRET_KEY_FILE`: exactly 32 raw random bytes (or a URL-safe base64 encoding of 32 bytes). This key encrypts provider secrets with AES-256-GCM and signs admin sessions. The application never auto-generates or replaces it.
- `WITG_ADMIN_PASSWORD_FILE`: one versioned salted scrypt record, never a plaintext password.

Startup and `/api/health` remain healthy when either file is absent, and the ordinary demo remains usable. Admin/settings or secret operations return sanitized `503` errors until configured. Back up the master key separately: losing it makes stored provider keys undecryptable.

Generate files **outside the repository and outside `WITG_DATA_DIR`**:

```bash
mkdir -p /secure/path/witg
uv run python -c "import secrets,sys; sys.stdout.buffer.write(secrets.token_bytes(32))" > /secure/path/witg/master-key
uv run python -c "from app.security import hash_admin_password; import getpass; print(hash_admin_password(getpass.getpass('Admin passphrase: ')))" > /secure/path/witg/admin-password.scrypt
```

Restrict both files to the service account. The scrypt format is `scrypt$v=1$n=...$r=...$p=...$salt=...$hash=...`; parsing enforces bounded parameters before hashing.

OpenAI-compatible endpoints may use HTTPS anywhere, or HTTP only when the exact hostname is operator-allowlisted and resolves exclusively to an intended private/LAN address. Userinfo, query strings, and fragments are rejected. Metadata, loopback, link-local, multicast, unspecified, and reserved addresses are always rejected—even for allowlisted names. Example LAN configuration:

```bash
WITG_MODEL_HOST_ALLOWLIST=modelbox.home.arpa,10.0.0.25
# Then an endpoint such as http://modelbox.home.arpa:8080/v1 is allowed.
```

Every outbound call resolves once under its total deadline, validates every returned address, and connects to the chosen validated IP. The original hostname remains in `Host` and TLS SNI, redirects are disabled, and `Connection: close` prevents a pooled IP connection from crossing hostname boundaries. This closes the validation/connection DNS-rebinding gap rather than relying on a second lookup. ElevenLabs remains restricted to its documented HTTPS origins on standard port 443.

Changing a model or voice endpoint clears its saved key unless a replacement is supplied in that same save. Discovery, test, and preview use a saved key only when the canonical requested URL exactly matches the saved URL; a different unsaved URL requires an explicit unsaved key.

## Provider limits

The process applies conservative in-memory controls before billable provider work:

- `WITG_PROVIDER_MAX_CONCURRENCY` (default `4`) limits simultaneous outbound calls globally.
- `WITG_PROVIDER_REQUEST_BUDGET` (default `120`) and `WITG_PROVIDER_BUDGET_WINDOW_SECONDS` (default `60`) bound outbound requests per process window.
- `WITG_PROVIDER_TOTAL_TIMEOUT_SECONDS` (default `20`) is a monotonic wall-clock deadline covering DNS, connection, and streamed response bodies; connect/read inactivity limits remain in force.
- `WITG_ANALYSIS_RATE_LIMIT` (default `10`) and `WITG_NARRATION_RATE_LIMIT` (default `6`) apply per trusted client identity during `WITG_RATE_LIMIT_WINDOW_SECONDS` (default `60`).
- Identical concurrent narration requests are single-flight by analysis/settings fingerprint, so only one TTS call is made.

These counters, windows, sessions, and single-flight coordination are process-local and **resets when the process restarts**. Multi-worker or multi-replica deployments need a shared limiter/budget service before public exposure. Limit responses are sanitized `429` or `503` errors; they do not include provider bodies or keys.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose only for the container path

## Local setup

```bash
uv sync
uv run pytest -q
```

Run without provider/admin files for healthy demo mode:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8787
```

Run with mounted/local files (Git Bash syntax):

```bash
WITG_DATA_DIR=C:/path/to/witg-data \
WITG_SECRET_KEY_FILE=C:/secure/witg/master-key \
WITG_ADMIN_PASSWORD_FILE=C:/secure/witg/admin-password.scrypt \
uv run uvicorn app.main:app --host 0.0.0.0 --port 8787
```

Open <http://localhost:8787/> for demo development and check <http://localhost:8787/api/health>. Use an HTTPS URL before unlocking Connections with real credentials.

## Connections workflow

1. Open **Connections** and unlock with the admin passphrase. The passphrase input is cleared immediately and is never placed in browser storage.
2. Configure an OpenAI-compatible HTTPS base URL. Refresh models or type a model ID manually, optionally test, then save.
3. Configure an official ElevenLabs origin and write-only key. Refresh voices and official TTS models, select each, preview/test with the complete unsaved selection, then save.
4. Submit bottle text or a photo. A complete model configuration uses `POST {base_url}/chat/completions`; otherwise the deterministic demo handles it.
5. Open a saved result and tap narration. Audio is generated on demand, cached by analysis text plus voice/TTS settings, and served only through opaque database-referenced URLs.

Blank key inputs preserve the current key. The separate clear buttons remove it. API responses expose only `api_key_configured`; plaintext and ciphertext are never returned.

## API summary

Ordinary routes remain open inside the LAN trust boundary:

- `GET /api/health`
- `POST /api/analyses`
- `POST /api/analyses/{id}/feedback`
- `POST /api/analyses/{id}/narration`
- `GET /api/audio/{opaque-token}`
- `GET /api/history?limit=50&offset=0`

Admin session routes:

- `POST /api/admin/login`
- `POST /api/admin/logout`
- `GET /api/admin/session`

All settings, discovery, test, and preview routes require the signed HttpOnly SameSite=Strict admin cookie. Mutations also require the in-memory CSRF token and exact Origin/Host validation:

- `GET /api/settings`
- `PUT /api/settings/model`
- `POST /api/settings/model/models`
- `POST /api/settings/model/test`
- `PUT /api/settings/voice`
- `POST /api/settings/voice/voices`
- `POST /api/settings/voice/models`
- `POST /api/settings/voice/test`
- `POST /api/settings/voice/preview`

Admin/settings responses are `Cache-Control: no-store`. Security headers include CSP without inline script permission, `frame-ancestors 'none'`, nosniff, and no-referrer.

## Docker Compose

The base Compose file is credential-free and starts healthy demo mode without any secret files:

```bash
docker compose up --build
```

For provider/admin setup, use the secrets override and required external paths. The `${VAR:?message}` declarations fail before startup when a path is missing; there is no source-tree default:

```bash
export WITG_SECRET_KEY_PATH=/secure/path/witg/master-key
export WITG_ADMIN_PASSWORD_PATH=/secure/path/witg/admin-password.scrypt
docker compose -f compose.yaml -f compose.secrets.yaml up --build
```

The Compose service maps port 8787, persists only `/data` in `witg-data`, and the override mounts both credential files read-only under `/run/secrets`. `secrets/` is ignored defensively, but credentials belong outside the repository. The base file configures the verified DadServer proxy source as `${WITG_TRUSTED_PROXY_IPS:-172.17.0.1}` for both Uvicorn and the app; override it only with the exact proxy IP/CIDR for the deployed bridge. Never use `*`.

```bash
docker compose down
# Only when intentional:
docker compose down --volumes
```

**Verification limitation:** Docker is not installed on Dadbase, so the image was not built or started on that workstation. Dockerfile/Compose contracts are covered statically; build verification belongs on a Docker-capable host.

## PWA behavior

The root-scoped service worker caches only the static shell, network-first. It bypasses every `/api/*` request, including settings and audio, so secrets, admin responses, analyses, and narration are never placed in the shell cache. JavaScript renders provider/API values with `textContent` and DOM construction; it does not use `innerHTML`, localStorage, or IndexedDB.

## Operator checks

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
node --check app/static/app.js
python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8787/api/health').read().decode())"
```

A healthy response is:

```json
{"status":"healthy","service":"whats-in-the-glass"}
```
