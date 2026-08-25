# Architecture

## Shape

What’s in the Glass? is one Python 3.11/FastAPI process serving a JSON/multipart API and no-build **LAN-local** PWA. SQLite persists analyses, feedback, singleton connection settings, and references to cached narration under `WITG_DATA_DIR`.

```text
Browser / installed PWA
  ├─ Leather Ledger HTML/CSS/JS
  ├─ open analysis/history/narration API
  └─ admin-authenticated Connections API
                    │
                    ▼
                 FastAPI
  ┌─────────────────┼───────────────────┐
  │ AdminAuth       │ Database          │
  │ AnalysisService │ ProviderClient    │
  │ NarrationService│ security middleware│
  └─────────┬───────┴─────────┬─────────┘
            │                 │
            ▼                 ▼
 WITG_DATA_DIR/       HTTPS provider APIs
 witg.sqlite3 + audio  (OpenAI-compatible / ElevenLabs)
```

`create_app` still accepts a replacement `AnalysisService`; existing embedding/tests can replace the runtime service. Without a replacement, `ConfiguredAnalysisService` selects the saved model dynamically and delegates to `DemoAnalysisService` until a model ID is complete. `PhotoInput` carries validated filename, MIME type, and bytes to either service.

## Connection and admin boundary

Every settings, discovery, connection-test, and voice-preview route requires an admin session. Admin setup is file-backed:

- `WITG_SECRET_KEY_FILE` points to a read-only 32-byte master key outside the data volume.
- `WITG_ADMIN_PASSWORD_FILE` points to a versioned, salted, bounded-scrypt hash record.

The plaintext passphrase is never stored. Login verification uses `hashlib.scrypt` and constant-time digest comparison. Failed attempts are bounded in memory. Successful login creates an in-memory session plus an HMAC-signed expiring cookie. The cookie is HttpOnly and SameSite=Strict, and Secure only when the ASGI scheme is HTTPS. The browser retains only the current CSRF token in JavaScript memory; it stores neither passwords nor tokens in localStorage/IndexedDB.

Admin mutations require the signed cookie, exact Origin/Host, and CSRF header. Login validates Origin. Admin/settings responses are `Cache-Control: no-store`. Plain HTTP credential entry fails with `https_required` unless the operator explicitly sets `WITG_ALLOW_INSECURE_CREDENTIALS=true`; that override is a temporary trusted-LAN compromise, not hardening.

Trusted proxy handling is explicit at both layers. Uvicorn reads `FORWARDED_ALLOW_IPS`; the app installs `ProxyHeadersMiddleware` with `WITG_TRUSTED_PROXY_IPS`. DadServer’s verified Docker host source is exactly `172.17.0.1`, never `*`. Only that trusted proxy can promote the ASGI scheme from forwarded HTTPS or replace `request.client` with a safely forwarded Tailscale address. Direct LAN clients keep their actual address, so spoofed forwarded headers cannot unlock credential writes. Login throttling keys by that trusted client identity; when the trusted proxy omits a client address, requests share the documented `unknown` proxy bucket.

Startup remains healthy/demo-capable with missing credential files. Admin and secret operations fail with sanitized `503` responses.

## Secret persistence

The singleton `connection_settings` row contains non-secret provider selections and encrypted key blobs. Provider keys are encrypted independently with AES-256-GCM. Each write uses a fresh 96-bit nonce and AAD bound to connection ID, secret field, and key version (`connection:1:<field>:v1`). Stored blobs contain a version prefix, nonce, ciphertext, and authentication tag—never plaintext.

Blank API-key inputs preserve the blob. Explicit clear removes it. A model or voice endpoint change clears the old key unless the request supplies a replacement atomically, preventing an old origin’s credential from being sent to a new origin. Existing `analyses` and `feedback` tables are created/migrated idempotently without destructive changes.

The master key is not auto-generated and is not stored in SQLite or `WITG_DATA_DIR`. Operators must back it up separately.

## Provider egress policy

`ProviderClient` is the only outbound HTTP layer and uses production `httpx` with injected transports/resolvers for deterministic tests. It preserves connect/read/write inactivity timeouts and adds one monotonic wall-clock deadline covering DNS, connection establishment, and every streamed body iteration. Responses have explicit JSON/audio size limits, redirects are disabled, and failures become sanitized `ProviderError` messages without provider bodies or credentials.

OpenAI-compatible URLs allow arbitrary HTTPS endpoints. HTTP is accepted only for an exact hostname in `WITG_MODEL_HOST_ALLOWLIST` whose resolved addresses are private/LAN. Metadata, loopback, link-local, multicast, unspecified, and reserved addresses are always rejected, even for allowlisted hosts. Non-allowlisted private addresses and all non-allowlisted HTTP endpoints are rejected.

Validation and connection are one operation: DNS runs once under the deadline, all answers are checked, and the request URL uses the chosen validated IP. The original hostname is preserved in `Host` and the `httpcore` `sni_hostname` extension for TLS SNI. `Connection: close` prevents connection reuse across distinct hostnames that happen to resolve to the same IP. This design does not perform a second DNS lookup and is covered by public-then-private rebinding and metadata-with-allowlist regressions.

Saved credentials are origin-bound during probes. Canonically equivalent saved URLs can reuse the stored key; a different unsaved endpoint must supply its own explicit write-only key. ElevenLabs is stricter: only the documented global, US, EU, India, and Singapore HTTPS hostnames on standard port 443 are accepted, with no arbitrary-host override.

- Models: `GET {base_url}/models`, optional `Authorization: Bearer`, safe `data[].id` output only.
- Analysis: `POST {base_url}/chat/completions`, JSON-object response mode, optional data-URI image content.
- Voices: `GET {base_url}/v2/voices`, `xi-api-key`, page size 100, `include_total_count=false`, bounded pagination driven by `has_more`/`next_page_token`, de-duplicated by `voice_id`, safe dropdown fields only.
- TTS models: `GET {base_url}/v1/models`, retaining only official entries with `can_do_text_to_speech=true`.
- TTS: `POST {base_url}/v1/text-to-speech/{voice_id}`, selected `model_id` and `output_format`, `xi-api-key`; preview accepts the complete unsaved first-time selection.

Generic chat completion has no standardized grounded-search capability. Model output is strictly validated against `AnalysisContent`; malformed output returns a sanitized `502` and is not saved. `sources=[]` is forced for configured-model analyses so no generated text is relabelled as verified citations.

A global semaphore bounds simultaneous provider requests. A process-wide sliding request budget bounds aggregate outbound calls, while separate per-client sliding windows protect public analysis and narration routes. All values are operator-configurable with conservative defaults. Narration uses a per-fingerprint single-flight lock and repeats the persistent cache check inside that lock, so concurrent identical requests produce one paid TTS call. These controls are lightweight in-process state: they reset on restart and do not coordinate across multiple workers/replicas. Capacity/budget failures are sanitized `503`; client rate failures are sanitized `429`.

## Analysis and narration flow

1. `POST /api/analyses` validates the drinker, conservative text lengths, request-body cap, MIME type, matching image signature, and 5 MiB photo cap.
2. `ConfiguredAnalysisService` checks the singleton settings on each request. With no model selected it preserves the deterministic demo fallback.
3. Text submissions become a user prompt. Photos become OpenAI-compatible multimodal user content with an in-request data URI.
4. Strict JSON is parsed and field-bounded before `Database.save_analysis`; provider failures never create fake rows.
5. `POST /api/analyses/{id}/narration` builds citation-free prose from the stored result.
6. A SHA-256 fingerprint binds the final prose, selected voice, endpoint, TTS model, and output format. The opaque digest names the MP3 and cache row.
7. Existing matching audio is reused. Changed analysis/voice/TTS settings produce a new fingerprint and generation.
8. `GET /api/audio/{token}` accepts only a 64-character hex token, requires a database reference, verifies the exact `<token>.mp3` filename under the narration directory, and never accepts arbitrary paths.

## Persistence

`Database` uses parameterized `sqlite3` statements and short-lived connections with foreign keys enabled. Tables:

- `analyses`: submission metadata, validated editorial fields, sources JSON, aware UTC timestamp;
- `feedback`: one optional rating/note row per analysis;
- `connection_settings`: singleton non-secret configuration and encrypted key blobs;
- `narrations`: analysis/fingerprint to opaque token and safe relative audio filename.

Local default is `data/`; Compose uses `WITG_DATA_DIR=/data` and a named volume. Credential files mount separately and read-only.

## Browser, service worker, and response safety

The semantic mobile UI uses static CSP-safe JavaScript. Dynamic values are assigned with `textContent`, form values, or `document.createElement`; `innerHTML` is not used. The Connections password/key inputs are write-only and cleared after use. No secret/session material is put in HTML, JavaScript storage, or URLs.

The root service worker is network-first for shell files and bypasses all `/api/` paths, including admin, settings, analysis, and audio. API/media responses are never copied into static caches.

Security middleware adds CSP (`frame-ancestors 'none'`, no inline script permission), `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`.

## Deployment trust boundary

The application provides no TLS termination and no per-user authorization for ordinary household analysis/history routes. **HTTPS is required before real credential entry**, normally through a locally trusted private reverse proxy. Insecure HTTP mode is an explicit short-lived trusted-LAN exception. Public exposure requires authentication for the whole app, rate limiting, trusted-proxy configuration, and a separate security review.

ChatGPT account OAuth, standardized web search, public internet exposure, and multi-user credentials remain deferred.
