# Model and Voice Connections Milestone

## Goal

Turn the verified What’s in the Glass foundation into a configurable LAN application whose model and voice providers are managed from a mobile Connections screen. The first supported adapters are OpenAI-compatible chat-completions APIs and ElevenLabs. ChatGPT account login is explicitly deferred because it requires a separate OAuth/account adapter.

## Product decisions

1. Add a third bottom-navigation destination: **Connections**.
2. Model provider options initially contain only `OpenAI-compatible`; the data model must allow later adapters.
3. Model settings:
   - display name;
   - base URL, default `https://api.openai.com/v1`;
   - write-only optional API key;
   - discovered-model dropdown plus manual model entry;
   - save, refresh models, and test connection actions.
4. Voice provider options initially contain only `ElevenLabs`; the data model must allow later adapters.
5. Voice settings:
   - base URL, default `https://api.elevenlabs.io`;
   - write-only API key;
   - voice dropdown populated from the authenticated ElevenLabs account;
   - selected voice ID and display name;
   - ElevenLabs TTS model, default `eleven_multilingual_v2`;
   - output format, default `mp3_44100_128`;
   - save, refresh voices, test connection, and preview voice actions.
6. Blank API-key fields preserve the existing secret. A separate explicit action clears a secret.
7. API responses expose only `api_key_configured: true|false`; secrets never return to the browser.
8. Credentials are encrypted at rest with AES-256-GCM using a 32-byte master key loaded from the read-only file configured by `WITG_SECRET_KEY_FILE`. The key is mounted outside the SQLite/data volume and is never stored in the image, XML environment values, source tree, or database.
9. All Connections/settings routes require an expiring admin session. A versioned salted scrypt password hash is loaded from the read-only file configured by `WITG_ADMIN_PASSWORD_FILE`; the plaintext passphrase is never stored in the container, SQLite, XML, environment, or browser storage. Ordinary drink entry, history, feedback, and playback remain frictionless on the trusted LAN.
10. The current demo analysis remains the fallback until a model connection is complete. Once complete, configured OpenAI-compatible chat completions power text/photo analysis.
11. Generic OpenAI-compatible chat completion does not imply grounded web search. Do not fabricate or relabel model-generated citations. The model adapter returns no research sources until a separate search/provider capability is implemented.
12. The selected ElevenLabs voice powers on-demand narration for saved analyses. Narration is cached under the persistent data directory and reused while the analysis text, voice, and TTS settings are unchanged.
13. The current HTTP deployment must visibly warn that credentials traverse the LAN without TLS. HTTPS is required before entering real credentials; insecure HTTP support is an explicitly documented temporary trusted-LAN compromise.

## API and persistence contract

### Persistence

Create an idempotent singleton settings table containing non-secret fields plus encrypted model/voice keys. Create a narration table (or equivalent deterministic metadata) that maps an analysis and voice/settings fingerprint to a persistent audio file. Existing `analyses` and `feedback` data must migrate without loss.

### Public settings view

`GET /api/settings` (admin session required)

Returns provider kinds, display names/base URLs, selected models/voices, output format, and write-only key-presence booleans. It never returns encrypted or plaintext secrets.

### Save settings

- `PUT /api/settings/model` (admin session + CSRF required)
- `PUT /api/settings/voice` (admin session + CSRF required)

Use validated JSON. API keys are optional write-only replacement values. Empty/missing keys preserve stored secrets. Explicit booleans permit clearing.

### Discovery and connection checks

- `POST /api/settings/model/models`
- `POST /api/settings/model/test`
- `POST /api/settings/voice/voices`
- `POST /api/settings/voice/test`
- `POST /api/settings/voice/preview`

All routes above require the admin session and CSRF protection for state-changing/provider-execution requests. Add `POST /api/admin/login`, `POST /api/admin/logout`, and `GET /api/admin/session`; use an expiring HttpOnly, SameSite=Strict cookie signed with the mounted master key.

Discovery/test requests may use an unsaved key supplied in the request or the stored key when omitted. Never echo a supplied key in success or error details.

OpenAI-compatible model discovery uses `GET {base_url}/models` with bearer authentication when configured. Chat analysis uses `POST {base_url}/chat/completions`. URL joining must avoid duplicate `/v1` segments.

ElevenLabs voice discovery uses `GET {base_url}/v2/voices?page_size=100&sort=name&sort_direction=asc`, authenticating with `xi-api-key`. Follow `has_more` and `next_page_token` with a bounded page count. Keep only safe dropdown fields such as `voice_id`, `name`, `category`, `description`, `preview_url`, and labels.

ElevenLabs TTS uses `POST {base_url}/v1/text-to-speech/{voice_id}?output_format=...`, JSON body with `text` and `model_id`, and `xi-api-key` authentication.

### Analysis and narration

Configured analysis must:

- support typed bottle names and data-URI image content for compatible vision models;
- request a strict JSON object matching the existing `AnalysisContent` fields;
- use bounded timeouts and response sizes;
- reject malformed provider output without saving a fake analysis;
- keep `sources=[]` unless a future grounded-search adapter supplies verified sources;
- give clear `502/503` errors without leaking secrets or raw provider bodies.

Narration endpoints must:

- build narration-safe prose from stored analysis fields without citations;
- generate only on demand;
- cache MP3 output persistently;
- reject use until ElevenLabs settings and a voice are complete;
- serve only database-referenced audio paths, never arbitrary filesystem paths.

## Security boundaries

- Only `http` and `https` model base URLs; reject credentials/userinfo, fragments, and query strings. Resolve and validate every destination on every call. Reject loopback, link-local, multicast, unspecified, metadata, and private addresses unless the operator explicitly allowlists the private destination in deployment configuration.
- ElevenLabs base URLs are restricted to its documented official global/US/EU/India/Singapore origins.
- Changing a model provider/base URL clears the old API key unless a replacement key is supplied atomically. Never use a credential on a new origin and never follow redirects.
- External requests use strict connect/read/total timeouts and bounded response bodies.
- JSON settings endpoints plus same-origin behavior; validate `Origin` on state-changing settings routes when present.
- No API keys in logs, exceptions, reprs, API responses, HTML, service-worker cache, or tests.
- Service worker must not cache `/api/*` or audio responses as static shell data.
- Audio filenames are opaque/deterministic hashes, not user text.
- Startup without the configured key/passphrase files remains healthy in demo mode, but admin/secret operations return a clear configuration error.
- Admin login uses constant-time comparison, bounded attempts, exact Origin/Host checks, CSRF tokens, and secure cookie settings when HTTPS is active.
- Responses include a strict CSP, `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`.

## Strict TDD tracer bullets

1. Failing DB tests for default settings, encrypted secret persistence, write-only reads, preserve-on-blank, clear, and migration; implement minimally.
2. Failing API tests for safe settings views and save validation; implement minimally.
3. Failing provider-client tests using `httpx.MockTransport` for model discovery/auth/timeouts/errors; implement minimally.
4. Failing provider-client tests for ElevenLabs pagination, voice mapping/auth/errors and preview audio; implement minimally.
5. Failing configured-analysis tests for text, image, strict output parsing, malformed output, and no fabricated sources; implement minimally.
6. Failing narration tests for selected voice, payload, cache reuse, stale-fingerprint regeneration, and safe serving; implement minimally.
7. Failing static/UI tests for Connections navigation, write-only key fields, model/voice discovery controls, preview, TLS warning, and no secret persistence in localStorage/service-worker; implement minimally.
8. Run full tests, Ruff check/format check, JavaScript syntax check, lock check, and independent specification/security/quality reviews.
9. Commit only after review approval.
10. Build on DadServer, back up the existing SQLite database and XML template, create separately mounted read-only master-key and admin-passphrase files outside the data volume, rebuild from the updated local image, recover cleanly from any Unraid rebuild race, and configure tailnet-only Tailscale Serve HTTPS from `https://dadserver.tail5b2d45.ts.net/` to the existing host port 3947. Keep the ordinary LAN HTTP URL for non-secret household use, reject secret entry there by default, and verify health/admin/settings/model/voice paths over HTTPS without exposing secrets.

## Explicit non-goals for this milestone

- ChatGPT subscription/account login or OAuth.
- A generic standardized web-search tool that does not exist.
- Public internet exposure.
- Multi-user authentication or per-user provider credentials.
- A full secrets manager.
