import base64
import socket

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.security import hash_admin_password

MASTER_KEY = base64.urlsafe_b64encode(b"test-provider-master-key-32bytes")


def public_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def login(client, password):
    return client.post(
        "/api/admin/login",
        json={"password": password},
        headers={"Origin": "http://testserver"},
    )


def test_admin_login_rejects_plain_http_by_default(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("correct horse battery staple"),
    )

    with TestClient(app) as client:
        response = login(client, "correct horse battery staple")

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": "https_required",
            "message": "HTTPS is required for credentials.",
        }
    }
    assert "correct horse" not in response.text


def test_forwarded_proto_header_cannot_bypass_plain_http_credential_gate(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("forwarded-header-canary"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/admin/login",
            json={"password": "forwarded-header-canary"},
            headers={
                "Origin": "http://testserver",
                "X-Forwarded-Proto": "https",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "https_required"


def test_exact_trusted_tailscale_proxy_can_supply_https_and_client_identity(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("tailscale-proxy-canary"),
        trusted_proxy_ips={"172.17.0.1"},
    )
    headers = {
        "Host": "dadserver.tail5b2d45.ts.net",
        "Origin": "https://dadserver.tail5b2d45.ts.net",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-For": "100.64.0.9",
    }

    with TestClient(app, client=("172.17.0.1", 42000)) as client:
        response = client.post(
            "/api/admin/login",
            json={"password": "tailscale-proxy-canary"},
            headers=headers,
        )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert "100.64.0.9" not in response.text


def test_login_throttling_uses_safely_forwarded_tailscale_client_identity(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("throttle-proxy-canary"),
        trusted_proxy_ips={"172.17.0.1"},
    )
    base_headers = {
        "Host": "dadserver.tail5b2d45.ts.net",
        "Origin": "https://dadserver.tail5b2d45.ts.net",
        "X-Forwarded-Proto": "https",
    }

    with TestClient(app, client=("172.17.0.1", 42000)) as client:
        for _ in range(5):
            response = client.post(
                "/api/admin/login",
                json={"password": "wrong"},
                headers={**base_headers, "X-Forwarded-For": "100.64.0.9"},
            )
            assert response.status_code == 401
        blocked = client.post(
            "/api/admin/login",
            json={"password": "wrong"},
            headers={**base_headers, "X-Forwarded-For": "100.64.0.9"},
        )
        separate_client = client.post(
            "/api/admin/login",
            json={"password": "wrong"},
            headers={**base_headers, "X-Forwarded-For": "100.64.0.10"},
        )

    assert blocked.status_code == 429
    assert separate_client.status_code == 401


def test_untrusted_lan_client_cannot_spoof_tailscale_forwarded_https(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("untrusted-proxy-canary"),
        trusted_proxy_ips={"172.17.0.1"},
    )

    with TestClient(app, client=("192.168.0.50", 42000)) as client:
        response = client.post(
            "/api/admin/login",
            json={"password": "untrusted-proxy-canary"},
            headers={
                "Host": "dadserver.tail5b2d45.ts.net",
                "Origin": "https://dadserver.tail5b2d45.ts.net",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "100.64.0.9",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin validation failed."


def test_insecure_override_supports_signed_admin_session_and_protected_settings(
    tmp_path,
):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("admin-canary-password"),
        allow_insecure_credentials=True,
    )

    with TestClient(app) as client:
        unauthorized = client.get("/api/settings")
        login_response = login(client, "admin-canary-password")
        session = client.get("/api/admin/session")
        settings = client.get("/api/settings")

    assert unauthorized.status_code == 401
    assert login_response.status_code == 200
    assert login_response.json()["authenticated"] is True
    assert login_response.json()["csrf_token"]
    cookie = login_response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert session.json() == {
        "authenticated": True,
        "csrf_token": login_response.json()["csrf_token"],
    }
    assert settings.status_code == 200
    assert settings.headers["cache-control"] == "no-store"
    assert settings.json()["model"]["api_key_configured"] is False
    assert "admin-canary-password" not in str(settings.json())


def test_model_settings_write_requires_csrf_origin_and_keeps_key_write_only(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("settings-admin-canary"),
        allow_insecure_credentials=True,
    )
    payload = {
        "provider": "openai_compatible",
        "display_name": "House model",
        "base_url": "https://models.example/v1",
        "model_id": "vision-1",
        "api_key": "model-api-key-canary",
    }

    with TestClient(app) as client:
        csrf = login(client, "settings-admin-canary").json()["csrf_token"]
        missing_csrf = client.put("/api/settings/model", json=payload)
        wrong_origin = client.put(
            "/api/settings/model",
            json=payload,
            headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.example"},
        )
        missing_origin = client.put(
            "/api/settings/model",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )
        saved = client.put(
            "/api/settings/model",
            json=payload,
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

    assert missing_csrf.status_code == 403
    assert wrong_origin.status_code == 403
    assert missing_origin.status_code == 403
    assert saved.status_code == 200
    assert saved.json()["api_key_configured"] is True
    assert "api_key" not in saved.json()
    assert "model-api-key-canary" not in saved.text
    assert app.state.database.get_model_api_key() == "model-api-key-canary"


def test_settings_validation_errors_never_echo_secret_input(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("validation-admin-canary"),
        allow_insecure_credentials=True,
    )
    with TestClient(app) as client:
        csrf = login(client, "validation-admin-canary").json()["csrf_token"]
        response = client.put(
            "/api/settings/model",
            json={
                "display_name": "Model",
                "base_url": "https://models.example/v1",
                "model_id": "model",
                "api_key": "validation-secret-canary" + "x" * 4096,
            },
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request."}
    assert "validation-secret-canary" not in response.text


def test_voice_settings_only_accept_official_elevenlabs_origins(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("voice-admin-canary"),
        allow_insecure_credentials=True,
    )
    payload = {
        "provider": "elevenlabs",
        "base_url": "https://api.eu.residency.elevenlabs.io",
        "voice_id": "voice-1",
        "voice_name": "Hamish",
        "tts_model_id": "eleven_multilingual_v2",
        "output_format": "mp3_44100_128",
        "api_key": "voice-api-key-canary",
    }

    with TestClient(app) as client:
        csrf = login(client, "voice-admin-canary").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        rejected = client.put(
            "/api/settings/voice",
            json={**payload, "base_url": "https://voice-proxy.example"},
            headers=headers,
        )
        saved = client.put("/api/settings/voice", json=payload, headers=headers)

    assert rejected.status_code == 422
    assert saved.status_code == 200
    assert saved.json()["voice_id"] == "voice-1"
    assert saved.json()["api_key_configured"] is True
    assert "voice-api-key-canary" not in saved.text


def test_authenticated_model_discovery_can_use_unsaved_write_only_key(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]}
        )

    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("discover-admin-canary"),
        allow_insecure_credentials=True,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    with TestClient(app) as client:
        csrf = login(client, "discover-admin-canary").json()["csrf_token"]
        response = client.post(
            "/api/settings/model/models",
            json={
                "base_url": "https://models.example/v1",
                "api_key": "unsaved-discovery-key-canary",
            },
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

    assert response.status_code == 200
    assert response.json() == {"models": [{"id": "model-a"}, {"id": "model-b"}]}
    assert (
        seen["request"].headers["authorization"]
        == "Bearer unsaved-discovery-key-canary"
    )
    assert "unsaved-discovery-key-canary" not in response.text


def test_model_probe_never_sends_saved_key_to_a_different_unsaved_endpoint(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("origin-binding-admin"),
        allow_insecure_credentials=True,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_model_settings(
        display_name="Saved",
        base_url="https://saved.example/v1/",
        model_id="saved-model",
        api_key="saved-model-origin-canary",
    )

    with TestClient(app) as client:
        csrf = login(client, "origin-binding-admin").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        same = client.post(
            "/api/settings/model/models",
            json={"base_url": "https://saved.example/v1"},
            headers=headers,
        )
        different = client.post(
            "/api/settings/model/models",
            json={"base_url": "https://different.example/v1"},
            headers=headers,
        )

    assert same.status_code == 200
    assert requests[0].headers["authorization"] == "Bearer saved-model-origin-canary"
    assert different.status_code == 503
    assert len(requests) == 1
    assert "saved-model-origin-canary" not in different.text


def test_voice_probe_never_sends_saved_key_to_a_different_unsaved_origin(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"voices": [], "has_more": False})

    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("voice-origin-admin"),
        allow_insecure_credentials=True,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id=None,
        voice_name=None,
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="saved-voice-origin-canary",
    )

    with TestClient(app) as client:
        csrf = login(client, "voice-origin-admin").json()["csrf_token"]
        response = client.post(
            "/api/settings/voice/voices",
            json={"base_url": "https://api.us.elevenlabs.io"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

    assert response.status_code == 503
    assert requests == []
    assert "saved-voice-origin-canary" not in response.text


def test_settings_reject_api_key_and_clear_api_key_conflict(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("clear-conflict-admin"),
        allow_insecure_credentials=True,
    )
    with TestClient(app) as client:
        csrf = login(client, "clear-conflict-admin").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        model = client.put(
            "/api/settings/model",
            json={
                "display_name": "Model",
                "base_url": "https://models.example/v1",
                "model_id": "model",
                "api_key": "replacement-model-key",
                "clear_api_key": True,
            },
            headers=headers,
        )
        voice = client.put(
            "/api/settings/voice",
            json={
                "base_url": "https://api.elevenlabs.io",
                "voice_id": None,
                "voice_name": None,
                "tts_model_id": "eleven_multilingual_v2",
                "output_format": "mp3_44100_128",
                "api_key": "replacement-voice-key",
                "clear_api_key": True,
            },
            headers=headers,
        )

    assert model.status_code == 422
    assert voice.status_code == 422
    assert app.state.database.get_model_api_key() is None
    assert app.state.database.get_voice_api_key() is None


def test_elevenlabs_origin_rejects_nonstandard_https_port(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("port-admin"),
        allow_insecure_credentials=True,
    )
    with TestClient(app) as client:
        csrf = login(client, "port-admin").json()["csrf_token"]
        response = client.put(
            "/api/settings/voice",
            json={
                "base_url": "https://api.elevenlabs.io:444",
                "voice_id": None,
                "voice_name": None,
                "tts_model_id": "eleven_multilingual_v2",
                "output_format": "mp3_44100_128",
            },
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

    assert response.status_code == 422


def test_model_settings_allow_http_only_for_exact_operator_allowlisted_private_host(
    tmp_path,
):
    def private_resolver(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.25", port))]

    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("http-model-admin"),
        allow_insecure_credentials=True,
        provider_resolver=private_resolver,
        model_host_allowlist={"modelbox.home.arpa"},
    )
    payload = {
        "display_name": "LAN model",
        "base_url": "http://modelbox.home.arpa:8080/v1",
        "model_id": "local-model",
    }
    with TestClient(app) as client:
        csrf = login(client, "http-model-admin").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        allowed = client.put("/api/settings/model", json=payload, headers=headers)
        blocked = client.put(
            "/api/settings/model",
            json={**payload, "base_url": "http://other.home.arpa:8080/v1"},
            headers=headers,
        )

    assert allowed.status_code == 200
    assert blocked.status_code == 422
    assert app.state.database.get_settings().model.base_url == payload["base_url"]


def test_authenticated_voice_preview_returns_audio_without_echoing_key(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ID3-preview-audio")

    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("preview-admin-canary"),
        allow_insecure_credentials=True,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="voice-1",
        voice_name="Hamish",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="preview-key-canary",
    )
    with TestClient(app) as client:
        csrf = login(client, "preview-admin-canary").json()["csrf_token"]
        response = client.post(
            "/api/settings/voice/preview",
            json={"text": "A short voice preview."},
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"ID3-preview-audio"
    assert b"preview-key-canary" not in response.content


def test_first_time_voice_models_and_preview_use_complete_unsaved_selection(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json=[
                    {
                        "model_id": "eleven_flash_v2_5",
                        "name": "Flash v2.5",
                        "can_do_text_to_speech": True,
                    }
                ],
            )
        return httpx.Response(200, content=b"ID3-first-time-preview")

    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("first-time-voice-admin"),
        allow_insecure_credentials=True,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    with TestClient(app) as client:
        csrf = login(client, "first-time-voice-admin").json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        models = client.post(
            "/api/settings/voice/models",
            json={
                "base_url": "https://api.elevenlabs.io",
                "api_key": "unsaved-first-time-key",
            },
            headers=headers,
        )
        preview = client.post(
            "/api/settings/voice/preview",
            json={
                "text": "First-time preview.",
                "base_url": "https://api.elevenlabs.io",
                "api_key": "unsaved-first-time-key",
                "voice_id": "unsaved-voice-id",
                "tts_model_id": "eleven_flash_v2_5",
                "output_format": "mp3_44100_128",
            },
            headers=headers,
        )

    assert models.status_code == 200
    assert models.json() == {
        "models": [{"model_id": "eleven_flash_v2_5", "name": "Flash v2.5"}]
    }
    assert preview.status_code == 200
    assert preview.content == b"ID3-first-time-preview"
    tts_request = requests[-1]
    assert tts_request.headers["xi-api-key"] == "unsaved-first-time-key"
    assert tts_request.url.path == "/v1/text-to-speech/unsaved-voice-id"
    assert tts_request.url.params["output_format"] == "mp3_44100_128"
    assert b'"model_id":"eleven_flash_v2_5"' in tts_request.content


def test_authenticated_voice_discovery_returns_only_safe_dropdown_fields(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "voice-1",
                        "name": "Hamish",
                        "category": "professional",
                        "settings": {"provider_internal": "omit"},
                    }
                ],
                "has_more": False,
            },
        )

    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        admin_password_record=hash_admin_password("voices-admin-canary"),
        allow_insecure_credentials=True,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    with TestClient(app) as client:
        csrf = login(client, "voices-admin-canary").json()["csrf_token"]
        response = client.post(
            "/api/settings/voice/voices",
            json={
                "base_url": "https://api.elevenlabs.io",
                "api_key": "voice-discovery-key-canary",
            },
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "voices": [
            {"voice_id": "voice-1", "name": "Hamish", "category": "professional"}
        ]
    }
    assert "voice-discovery-key-canary" not in response.text
    assert "provider_internal" not in response.text
