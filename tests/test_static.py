from fastapi.testclient import TestClient

from app.main import create_app


def test_home_shell_is_accessible_and_text_first(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '<link rel="stylesheet" href="/static/styles.css">' in html
    assert '<script src="/static/app.js" defer></script>' in html
    assert 'aria-label="Choose drinker"' in html
    assert '<label for="drinkName">Bottle name</label>' in html
    assert html.index("Tell me about it") < html.index("Photograph the label")
    assert 'accept="image/jpeg,image/png,image/webp"' in html
    assert 'capture="environment"' in html
    assert 'id="guestName"' in html
    assert 'aria-live="polite"' in html
    assert "Hamish narration is the next integration" in html
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
    assert '"/static/app.js"' in worker
    assert '"/static/styles.css"' in worker
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
    assert 'navigator.serviceWorker.register("/service-worker.js")' in script


def test_mobile_navigation_stays_above_scrollable_content(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        styles = client.get("/static/styles.css").text

    bottom_rule = styles.split(".bottom{", 1)[1].split("}", 1)[0]
    assert "z-index:" in bottom_rule
