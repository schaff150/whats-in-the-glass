from fastapi.testclient import TestClient

from app.main import create_app


def test_home_shell_is_accessible_and_text_first(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '<link rel="stylesheet" href="/static/styles.css?v=8">' in html
    assert '<script src="/static/app.js?v=8" defer></script>' in html
    assert response.headers["cache-control"] == "no-cache"
    assert 'aria-label="Choose drinker"' in html
    assert 'data-drinker="Me" aria-pressed="true">me</button>' in html
    assert 'data-drinker="Wife" aria-pressed="false">Gin</button>' in html
    assert 'data-drinker="Other" aria-pressed="false">guest</button>' in html
    assert '<label for="drinkName">Bottle name</label>' in html
    assert html.index("Tell me about it") < html.index("Photograph the label")
    assert 'accept="image/jpeg,image/png,image/webp"' in html
    assert 'capture="environment"' in html
    assert 'id="guestName"' in html
    assert 'aria-live="polite"' in html
    assert "Choose a voice in Connections" in html
    assert 'id="audioButton"' in html and "disabled" in html


def test_client_uses_real_analysis_api_and_safe_dom_rendering(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/static/app.js")

    assert response.status_code == 200
    script = response.text
    assert 'fetch("/api/analyses"' in script
    assert "new FormData()" in script
    assert ".textContent" in script
    assert "document.createElement" in script
    assert "innerHTML" not in script
    assert "guestRow.hidden" in script
    assert 'photoInput.addEventListener("change"' in script


def test_client_displays_profile_labels_in_history(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        script = client.get("/static/app.js").text

    assert 'Me: "me"' in script
    assert 'Wife: "Gin"' in script
    assert 'Other: "guest"' in script
    assert "function displayDrinker(analysis)" in script
    assert "const drinker = displayDrinker(latest);" in script
    assert "const who = displayDrinker(analysis);" in script


def test_client_fetches_feedback_and_recent_history(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        script = client.get("/static/app.js").text

    assert "/feedback`" in script
    assert 'fetch("/api/history"' in script
    assert "saveFeedback" in script
    assert "historyList.replaceChildren" in script
    assert "No recent glasses yet" in script
    assert "Saved to your recent glasses" in script
    assert "innerHTML" not in script


def test_recent_glasses_reopen_the_saved_review_and_cached_narration(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        script = client.get("/static/app.js").text
        styles = client.get("/static/styles.css").text

    assert 'const card = document.createElement("button")' in script
    assert 'card.type = "button"' in script
    assert (
        'card.setAttribute("aria-label", `Open review for ${analysis.identified_title}`)'
        in script
    )
    assert 'card.addEventListener("click", () => renderAnalysis(analysis))' in script
    assert "Play narration. Saved audio is reused when available." in script
    history_rule = styles.split(".history-card{", 1)[1].split("}", 1)[0]
    assert "text-align:left" in history_rule
    assert "width:100%" in history_rule


def test_manifest_icons_and_service_worker_are_served(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        manifest_response = client.get("/static/manifest.webmanifest")
        worker_response = client.get("/static/service-worker.js")
        icon_responses = {
            name: client.get(f"/static/icons/{name}")
            for name in [
                "icon.svg",
                "icon-180.png",
                "icon-192.png",
                "icon-512.png",
                "icon-maskable-192.png",
                "icon-maskable-512.png",
                "favicon.ico",
            ]
        }

    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["name"] == "What’s in the Glass?"
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert {icon["sizes"] for icon in manifest["icons"]} >= {"192x192", "512x512"}
    maskable_icons = [
        icon for icon in manifest["icons"] if "maskable" in icon.get("purpose", "")
    ]
    assert {icon["src"] for icon in maskable_icons} == {
        "/static/icons/icon-maskable-192.png",
        "/static/icons/icon-maskable-512.png",
    }
    assert all(response.status_code == 200 for response in icon_responses.values())
    assert icon_responses["icon-maskable-192.png"].content[25] in {2, 3}

    assert worker_response.status_code == 200
    worker = worker_response.text
    assert 'const CACHE_NAME = "witg-shell-v8"' in worker
    assert '"/static/app.js?v=8"' in worker
    assert '"/static/styles.css?v=8"' in worker
    assert 'url.pathname.startsWith("/api/")' in worker
    assert "fetch(event.request)" in worker
    assert "cache.put" in worker
    assert worker.index("fetch(event.request)") < worker.index(
        "caches.match(event.request)"
    )
    with TestClient(create_app(data_dir=tmp_path)) as client:
        script = client.get("/static/app.js").text
    assert "navigator.serviceWorker.register" in script


def test_service_worker_is_registered_at_the_app_scope(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        worker_response = client.get("/service-worker.js")
        script = client.get("/static/app.js").text

    assert worker_response.status_code == 200
    assert worker_response.headers["content-type"].startswith("text/javascript")
    assert worker_response.headers["cache-control"] == "no-cache"
    assert 'navigator.serviceWorker.register("/service-worker.js")' in script


def test_mobile_navigation_stays_above_scrollable_content(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        styles = client.get("/static/styles.css").text

    bottom_rule = styles.split(".bottom{", 1)[1].split("}", 1)[0]
    assert "z-index:" in bottom_rule


def test_connections_ui_is_mobile_accessible_write_only_and_csp_safe(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        html = client.get("/").text
        script = client.get("/static/app.js").text
        worker = client.get("/service-worker.js").text

    assert 'id="connectionsView"' in html
    assert 'data-view="connectionsView"' in html
    assert ">Connections</button>" in html
    assert 'id="adminPassword"' not in html
    assert 'id="modelApiKey"' in html and 'id="voiceApiKey"' in html
    assert "Blank preserves the saved key" in html
    assert 'id="clearModelKey"' in html and 'id="clearVoiceKey"' in html
    assert 'id="refreshModels"' in html and 'id="testModel"' in html
    assert 'id="refreshVoices"' in html and 'id="testVoice"' in html
    assert 'id="refreshVoiceModels"' in html
    assert 'list="ttsModelOptions"' in html
    assert 'id="previewVoice"' in html
    assert "Trusted LAN mode" in html
    for endpoint in [
        "/api/settings/model",
        "/api/settings/model/models",
        "/api/settings/model/test",
        "/api/settings/voice",
        "/api/settings/voice/voices",
        "/api/settings/voice/models",
        "/api/settings/voice/test",
        "/api/settings/voice/preview",
        "/narration",
    ]:
        assert endpoint in script
    assert "innerHTML" not in script
    assert "/api/admin/" not in script
    assert "csrfToken" not in script
    assert "localStorage" not in script
    assert "indexedDB" not in script
    assert 'url.pathname.startsWith("/api/")' in worker
    assert '"/api/' not in worker.split("SHELL_ASSETS", 1)[1].split("];", 1)[0]


def test_connections_client_omits_keys_for_clear_and_never_saves_voice_placeholder(
    tmp_path,
):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        html = client.get("/").text
        script = client.get("/static/app.js").text

    assert 'option.value = settings.voice.voice_id || ""' in script
    assert 'option.dataset.placeholder = "true"' in script
    assert "voiceSelect.selectedOptions[0]?.dataset.placeholder" in script
    assert "api_key: clearApiKey" in script
    assert script.count("? null") >= 2
    assert "Refresh voices to choose</option>" not in html


def test_client_renders_structured_errors_and_preview_revokes_blob_on_all_outcomes(
    tmp_path,
):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        script = client.get("/static/app.js").text

    assert 'typeof payload?.detail?.message === "string"' in script
    # Provider, draft selection and key isolation execute in test_frontend_runtime.py.
    assert 'preview.addEventListener("error", revokePreviewUrl' in script
    assert 'preview.addEventListener("ended", revokePreviewUrl' in script
    assert "URL.revokeObjectURL(url)" in script


def test_analysis_ui_wording_is_neutral_for_demo_and_configured_models(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        html = client.get("/").text
        script = client.get("/static/app.js").text

    assert "none in demo" not in script
    assert "No grounded sources" in script
    assert "identification-pending" not in html
    assert "honest demo briefing" not in html
    assert "configured multimodal model" in html
