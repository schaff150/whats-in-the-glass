# Architecture

## Shape

What’s in the Glass? is one Python 3.11/FastAPI process serving both a JSON/multipart API and a no-build PWA. It is designed for a trusted **LAN-local** deployment. SQLite keeps the state intentionally lightweight: recent analyses and optional feedback, not cellar inventory.

```text
Browser / installed PWA
  ├─ static HTML, CSS, JavaScript, manifest and icons
  └─ /api multipart + JSON requests
             │
             ▼
          FastAPI
  ┌──────────┴───────────┐
  │ AnalysisService      │  replaceable provider seam
  │ Database             │  parameterized sqlite3 queries
  └──────────┬───────────┘
             ▼
  WITG_DATA_DIR/witg.sqlite3
```

## Request flow

1. `POST /api/analyses` accepts `Me`, `Wife`, or `Other`, optional guest name, and at least one of bottle text or a photograph.
2. The API validates supported image MIME types and reads at most 5 MiB plus one byte before accepting an upload.
3. The narrow `AnalysisService` protocol returns briefing content. `DemoAnalysisService` is deterministic and explicitly does no AI inference, image recognition, search, or TTS.
4. The API combines the submission metadata and service result into `AnalysisCreate` and persists it through `Database`.
5. `POST /api/analyses/{id}/feedback` validates an optional 1–5 rating and optional note, then stores one feedback row per analysis.
6. `GET /api/history` returns analyses newest first with feedback joined.

The application factory accepts a replacement `AnalysisService`, keeping future network integrations outside route and persistence code.

## Persistence

`Database` uses Python’s standard `sqlite3` module and parameterized placeholders for all values. The `analyses` table contains identity, submission metadata, editorial sections, source JSON, and an aware UTC creation timestamp. The `feedback` table has a one-to-one key to an analysis and stores optional rating and notes.

The database path is `${WITG_DATA_DIR}/witg.sqlite3`; local default is `data/`, while Compose sets `WITG_DATA_DIR=/data` and maps a named volume there. SQLite connections are short-lived per operation, which is suitable for this low-concurrency household application.

## PWA and safety

The interface is semantic static HTML with Leather Ledger CSS and plain JavaScript. API values are assigned with `textContent` or appended through DOM construction; dynamic data is never passed to HTML parsing. All primary controls meet a 44px minimum touch target.

The root-scoped service worker pre-caches a fixed list of shell files. It does not dynamically populate the cache. Requests under `/api/` bypass interception and remain **network-first**, preventing stale analysis, rating, or history responses from masquerading as current state.

The application has no authentication, authorization, or TLS termination. Its trust boundary is the private LAN; public exposure requires a separate authenticated reverse proxy and an explicit security review.

## Integration seam

The next milestone supplies an `AnalysisService` implementation for a configurable multimodal model and grounded search. That service should return the existing `AnalysisContent` shape with real citations, while API routes and SQLite remain unchanged. Once final researched copy is persisted, a separate cached audio component can call ElevenLabs for Hamish narration. The PWA audio card remains disabled until that real media endpoint exists.
