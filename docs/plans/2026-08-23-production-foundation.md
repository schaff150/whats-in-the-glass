# What’s in the Glass? Production Foundation Implementation Plan

> **For Hermes:** Use subagent-driven-development to implement this plan task-by-task, following strict RED–GREEN–REFACTOR testing.

**Goal:** Build the first production-shaped vertical slice: a runnable LAN-local FastAPI/PWA application with the chosen Leather Ledger design, text-first and photo-secondary input, real SQLite history/ratings, and a replaceable demo analysis service.

**Architecture:** A single FastAPI process serves a no-build mobile PWA and a small JSON/multipart API. SQLite under a configurable data directory stores analyses and feedback. A narrow `AnalysisService` protocol isolates the current deterministic demo response from the later model/search implementation; no model or ElevenLabs credentials are required in this milestone.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite, plain semantic HTML/CSS/JavaScript, pytest/httpx, uv, Docker.

---

## Locked product decisions

- Name: **What’s in the Glass?**
- Visual direction: **Leather Ledger** — saddle leather, cream paper, brass/amber details, serif-led editorial results.
- Input hierarchy: text entry and its primary submit button appear first; photograph/upload is an equally clear but visually secondary option beneath it.
- Drinkers: Me, Wife, Other. Other reveals an optional guest name.
- Feedback: optional 1–5 rating plus one optional likes/dislikes note.
- Tracking stays lightweight: recent history, not cellar inventory.
- App/data remain LAN-local; later model/search/TTS calls may leave the LAN.
- Model and TTS provider remain unselected and configurable.

## Milestone acceptance criteria

1. `uv run pytest -q` passes with no warnings.
2. `uv run uvicorn app.main:app` serves a working app and `/api/health` reports healthy.
3. A user can select a drinker, submit a bottle name, receive a demo researched briefing, rate it, and see it in Recent Glasses after refresh.
4. An image-only multipart submission is accepted and clearly marked as a demo photo identification rather than falsely claiming real vision inference.
5. Empty submissions, invalid files, and ratings outside 1–5 are rejected with useful errors.
6. The UI preserves the selected Leather Ledger design, uses text-first hierarchy, has at least 44px touch targets, and safely renders API data without `innerHTML`.
7. PWA manifest, service worker, favicon, Apple touch icon, and 192/512 icons are served.
8. Dockerfile and compose configuration exist and expose the app on port 8787 with `/data` persistence. Docker build verification may be deferred because Docker is not installed on Dadbase; this limitation must be explicit.

---

### Task 1: Create the tested application shell

**Objective:** Establish installable dependencies, app factory, static-file serving, health endpoint, and test fixture.

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `tests/conftest.py`
- Create: `tests/test_health.py`

**RED:** Write `test_health_returns_service_status` and `test_root_serves_html`; run them and verify import/route failure.

**GREEN:** Create the minimal FastAPI app with `GET /api/health`, static mount, and `GET /` returning `app/static/index.html`. Provide a temporary minimal HTML shell only after the failing root test exists.

**VERIFY:** `uv run pytest tests/test_health.py -q`.

### Task 2: Add SQLite analysis and feedback persistence

**Objective:** Persist generated analyses and their optional feedback in a local SQLite database.

**Files:**
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `tests/test_db.py`

**RED/GREEN slices:**
1. Initialize schema and save/get an analysis.
2. Save a 1–5 rating and note using a parameterized query.
3. List analyses newest first with feedback joined.
4. Reject ratings outside 1–5 at the Pydantic boundary.

**Data fields:** ID, drinker, optional guest name, submitted drink name, identified title, region, age statement, ABV, style, three editorial sections, sources, created timestamp, optional rating, optional note.

**VERIFY:** `uv run pytest tests/test_db.py -q`.

### Task 3: Add replaceable analysis service and API

**Objective:** Complete a real request→service→database→response path without pretending the demo service is live AI research.

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/analysis.py`
- Create: `app/api.py`
- Create: `tests/test_api.py`
- Modify: `app/main.py`

**Endpoint contract:**
- `POST /api/analyses` as multipart fields: `drinker`, optional `guest_name`, optional `drink_name`, optional `photo`.
- Require at least one of drink name or photo.
- Permit `Me`, `Wife`, or `Other`; guest name is meaningful only for Other.
- Accept image MIME types JPEG, PNG, or WebP with a conservative size limit.
- `POST /api/analyses/{id}/feedback` JSON: rating and notes.
- `GET /api/history` returns newest first.

**Demo-service honesty:** Text input may return the approved Glenrothes sample when recognized; unknown text gets a clearly labelled demonstration briefing using the submitted name. Photo-only input returns “Bottle photograph — identification pending” and never invents a bottle identity.

**RED/GREEN slices:** text analysis, photo-only analysis, empty rejection, bad MIME rejection, feedback persistence, history listing.

**VERIFY:** `uv run pytest tests/test_api.py -q` then full suite.

### Task 4: Consolidate Leather Ledger into the production PWA

**Objective:** Replace mock-only JavaScript with a real API-driven mobile interface.

**Files:**
- Create/replace: `app/static/index.html`
- Create: `app/static/styles.css`
- Create: `app/static/app.js`
- Copy: `app/static/icons/icon.svg`, `icon-180.png`, `icon-192.png`, `icon-512.png`, `favicon.ico`
- Create: `app/static/manifest.webmanifest`
- Create: `app/static/service-worker.js`
- Create: `tests/test_static.py`

**UI requirements:**
- Text entry card and “Tell me about it” are the first input action.
- “Photograph the label” is directly below as a secondary action using `<input type="file" accept="image/jpeg,image/png,image/webp" capture="environment">`.
- Other reveals an optional guest-name field.
- Loading, error, result, audio-unavailable, saved, and empty-history states are explicit.
- The audio card is visibly disabled and says Hamish narration is the next integration; do not simulate audio.
- Render all remote/API values with `textContent` or DOM construction, never `innerHTML`.
- Recent list is loaded from the API and remains intentionally simple.
- Service worker caches only the static shell; API requests remain network-first.

**RED:** Static contract tests assert manifest/icons/service worker and key accessible labels are served.

**VERIFY:** Full pytest suite plus browser interaction at 430×932.

### Task 5: Add Docker and operator documentation

**Objective:** Make the milestone ready to run locally and ready for later DadServer deployment.

**Files:**
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `README.md`
- Create: `docs/architecture.md`

**Requirements:**
- Non-root runtime user.
- Port 8787.
- `WITG_DATA_DIR=/data` with volume mapping.
- Health check calls `/api/health` without curl.
- No credentials committed.
- README documents uv setup, local run, tests, Docker commands, current demo limitations, and the next integration seam for model/search/ElevenLabs.

**VERIFY:** Parse compose YAML if PyYAML is available; inspect Dockerfile; run local uvicorn health check. Record that Dadbase has no Docker CLI, so image build is not verified here.

### Task 6: Final independent review and packaged stopping point

**Objective:** Prove the vertical slice works and package it for the user.

**Checks:**
- Full pytest suite.
- Ruff if available.
- Secret/shell/eval/unsafe-SQL scan.
- Independent spec-compliance review.
- Independent code-quality/security review.
- Browser QA of text analysis, Other profile, result, rating, and history at a mobile viewport.
- Generate current screenshots and a ZIP excluding `.git`, `.venv`, caches, database files, and secrets.
- Send summary, screenshots, ZIP, verified state, known limitation, and exact next milestone to Discord `1439262895405011056`.

**Next milestone after this stopping point:** connect a real multimodal model plus grounded web research, then generate and cache ElevenLabs Hamish narration.
