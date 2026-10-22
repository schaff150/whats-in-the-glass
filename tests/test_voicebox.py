import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers import ProviderClient, ProviderError

VOICEBOX = "http://voicebox.home.arpa:17600"
VOICE_SETTINGS = {
    "provider": "voicebox",
    "base_url": VOICEBOX,
    "voice_id": "hamish-profile",
    "voice_name": "Hamish",
    "tts_model_id": "luxtts",
    "output_format": "mp3",
}


def private_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.87", port))]


def test_voicebox_narration_needs_no_secret_and_caches_mp3(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url == "http://192.168.0.87:17600/v1/audio/speech"
        assert request.headers["host"] == "voicebox.home.arpa:17600"
        assert "xi-api-key" not in request.headers
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body == {
            "model": "luxtts",
            "voice": "hamish-profile",
            "input": body["input"],
            "response_format": "mp3",
        }
        assert "House pour" in body["input"]
        return httpx.Response(
            200, headers={"Content-Type": "audio/mpeg"}, content=b"ID3-local-hamish"
        )

    app = create_app(
        data_dir=tmp_path,
        provider_resolver=private_resolver,
        provider_transport=httpx.MockTransport(handler),
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    with TestClient(app) as client:
        saved = client.put(
            "/api/settings/voice",
            json=VOICE_SETTINGS,
            headers={"Origin": "http://testserver"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["provider"] == "voicebox"
        assert saved.json()["display_name"] == "Voicebox"
        assert saved.json()["api_key_configured"] is False
        analysis_id = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "House pour"}
        ).json()["id"]
        first = client.post(f"/api/analyses/{analysis_id}/narration")
        assert first.status_code == 200, first.text
        second = client.post(f"/api/analyses/{analysis_id}/narration")
        assert second.json() == first.json()
        audio = client.get(first.json()["audio_url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/mpeg")
        assert audio.content == b"ID3-local-hamish"
        assert (
            client.get("/api/history").json()[0]["narration_url"]
            == first.json()["audio_url"]
        )
    restarted = create_app(
        data_dir=tmp_path,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=private_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    with TestClient(restarted) as client:
        replay = client.post(f"/api/analyses/{analysis_id}/narration")
        assert replay.json() == first.json()
        assert client.get(replay.json()["audio_url"]).content == b"ID3-local-hamish"
    assert len(requests) == 1


def test_unsaved_voicebox_discovery_and_preview_never_read_saved_elevenlabs_key(
    tmp_path,
):
    requests = []

    def handler(request):
        requests.append(request)
        assert "xi-api-key" not in request.headers
        assert "authorization" not in request.headers
        if request.url.path == "/profiles":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "hamish-profile",
                        "name": "Hamish",
                        "default_engine": "luxtts",
                        "private_detail": "must-not-echo",
                    }
                ],
            )
        if request.url.path == "/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": "luxtts", "description": "LuxTTS"}]}
            )
        assert request.url.path == "/v1/audio/speech"
        assert json.loads(request.content) == {
            "voice": "hamish-profile",
            "model": "luxtts",
            "input": "A local preview.",
            "response_format": "mp3",
        }
        return httpx.Response(
            200, headers={"Content-Type": "audio/mpeg"}, content=b"ID3-preview"
        )

    app = create_app(
        data_dir=tmp_path,
        secret_key=b"v" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=private_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="old-voice",
        voice_name="Old",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="old-key-canary",
    )

    def forbidden_key_read():
        raise AssertionError("Voicebox must never decrypt the old cloud credential")

    app.state.database.get_voice_api_key = forbidden_key_read
    probe = {"provider": "voicebox", "base_url": VOICEBOX}
    headers = {"Origin": "http://testserver"}
    with TestClient(app) as client:
        voices = client.post("/api/settings/voice/voices", json=probe, headers=headers)
        assert voices.status_code == 200, voices.text
        assert voices.json() == {
            "voices": [{"voice_id": "hamish-profile", "name": "Hamish"}]
        }
        models = client.post("/api/settings/voice/models", json=probe, headers=headers)
        assert models.status_code == 200, models.text
        assert models.json() == {"models": [{"model_id": "luxtts", "name": "LuxTTS"}]}
        tested = client.post("/api/settings/voice/test", json=probe, headers=headers)
        assert tested.status_code == 200, tested.text
        assert tested.json() == {"ok": True, "voices_count": 1}
        preview = client.post(
            "/api/settings/voice/preview",
            json={
                **probe,
                "voice_id": "hamish-profile",
                "tts_model_id": "luxtts",
                "output_format": "mp3",
                "text": "A local preview.",
            },
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert preview.content == b"ID3-preview"
        assert preview.headers["content-type"].startswith("audio/mpeg")
        assert preview.headers["cache-control"] == "no-store"
        assert client.get("/api/settings").json()["voice"]["provider"] == "elevenlabs"
    assert len(requests) == 4


@pytest.mark.parametrize(
    ("media_type", "content"),
    [
        ("application/json", b'{"error":"private-provider-error"}'),
        ("audio/wav", b"RIFF-not-an-mp3"),
        ("audio/mpeg", b"not-an-mp3"),
        ("audio/mpeg", b""),
    ],
)
def test_voicebox_rejects_non_mp3_without_caching_or_cloud_fallback(
    tmp_path, media_type, content
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"Content-Type": media_type}, content=content
        )

    app = create_app(
        data_dir=tmp_path,
        provider_resolver=private_resolver,
        provider_transport=httpx.MockTransport(handler),
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    app.state.database.update_voice_settings(**VOICE_SETTINGS)
    with TestClient(app) as client:
        analysis_id = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Failure probe"}
        ).json()["id"]
        response = client.post(f"/api/analyses/{analysis_id}/narration")
        assert response.status_code == 502, response.text
        assert response.json() == {"detail": "Narration could not be generated."}
        assert client.get("/api/history").json()[0]["narration_url"] is None
    with app.state.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM narrations").fetchone()[0] == 0
    assert not (tmp_path / "narration").exists()
    assert len(requests) == 1
    assert requests[0].headers["host"] == "voicebox.home.arpa:17600"


def test_switch_to_voicebox_clears_cloud_key_preserves_model_and_survives_restart(
    tmp_path,
):
    app = create_app(
        data_dir=tmp_path,
        secret_key=b"k" * 32,
        provider_resolver=private_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    database = app.state.database
    database.update_model_settings(
        display_name="Existing model",
        base_url="https://model.example/v1",
        model_id="existing-model",
        api_key="model-key-canary",
    )
    database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="old-voice",
        voice_name="Old",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="old-key-canary",
    )
    model_before = database.get_settings().model
    with TestClient(app) as client:
        assert client.put("/api/settings/voice", json=VOICE_SETTINGS).status_code == 403
        saved = client.put(
            "/api/settings/voice",
            json=VOICE_SETTINGS,
            headers={"Origin": "http://testserver"},
        )
        assert saved.status_code == 200
        assert saved.json()["api_key_configured"] is False
    assert database.get_voice_api_key() is None
    assert database.get_model_api_key() == "model-key-canary"
    assert database.get_settings().model == model_before
    # A restart with no master key must still be able to read the local selection.
    restarted = create_app(data_dir=tmp_path)
    with TestClient(restarted) as client:
        assert client.get("/api/settings").json()["voice"] == saved.json()


@pytest.mark.parametrize("path", ["voices", "models", "test", "preview"])
def test_voicebox_probes_cannot_reuse_model_allowlist_or_send_api_keys(tmp_path, path):
    calls = []
    app = create_app(
        data_dir=tmp_path,
        provider_resolver=private_resolver,
        provider_transport=httpx.MockTransport(lambda request: calls.append(request)),
        model_host_allowlist={"voicebox.home.arpa"},
    )
    with TestClient(app) as client:
        probe = {**VOICE_SETTINGS, "text": "Local only."}
        response = client.post(
            f"/api/settings/voice/{path}",
            json=probe,
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 502, response.text
        invalid_key = client.post(
            f"/api/settings/voice/{path}",
            json={**probe, "api_key": "do-not-send-canary"},
            headers={"Origin": "http://testserver"},
        )
        assert invalid_key.status_code == 422
        assert "do-not-send-canary" not in invalid_key.text
        rejected_save = client.put(
            "/api/settings/voice", json=probe, headers={"Origin": "http://testserver"}
        )
        assert rejected_save.status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "93.184.216.34", "0.0.0.0", "224.0.0.1"]
)
def test_voicebox_allowlist_still_rejects_non_lan_and_forbidden_addresses(
    tmp_path, address
):
    calls = []

    def resolver(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    app = create_app(
        data_dir=tmp_path,
        provider_resolver=resolver,
        provider_transport=httpx.MockTransport(lambda request: calls.append(request)),
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/voice",
            json=VOICE_SETTINGS,
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 422
        assert client.get("/api/settings").json()["voice"]["provider"] == "elevenlabs"
    assert calls == []


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_voicebox_deadline_closes_real_socket_and_releases_capacity(monkeypatch, phase):
    disconnected = threading.Event()
    partial_sent = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((body, dict(self.headers)))
            if phase == "body":
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.flush()
            time.sleep(0.3)
            self.wfile.write(b"HTTP/1.1 200 OK\r\n" if phase == "headers" else b"ID3")
            self.wfile.flush()
            partial_sent.set()
            self.connection.settimeout(2)
            if self.connection.recv(1) == b"":
                disconnected.set()

        def log_message(self, *args):
            pass

    monkeypatch.setenv("NO_PROXY", "*")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = ProviderClient(
        resolver=private_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
        voicebox_speech_total_timeout=0.6,
        max_concurrency=1,
    )
    monkeypatch.setattr(
        provider,
        "_url",
        lambda *args: f"http://127.0.0.1:{server.server_port}/v1/audio/speech",
    )
    try:
        started = time.monotonic()
        with pytest.raises(ProviderError, match="timed out"):
            provider.synthesize_speech(
                provider="voicebox",
                base_url=VOICEBOX,
                api_key=None,
                voice_id="hamish-profile",
                model_id="luxtts",
                output_format="mp3",
                text="A bounded local narration.",
            )
        assert time.monotonic() - started < 0.9
        assert partial_sent.is_set(), "The real blocked read must be reached"
        assert disconnected.wait(0.3), "Expiry must close the actual socket"
        assert len(requests) == 1
        body, headers = requests[0]
        assert body == {
            "model": "luxtts",
            "voice": "hamish-profile",
            "input": "A bounded local narration.",
            "response_format": "mp3",
        }
        assert "xi-api-key" not in {key.lower() for key in headers}
        with provider._provider_slot():
            pass
    finally:
        provider.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("status", [302, 401, 503])
def test_voicebox_http_failure_never_falls_back_or_follows_redirect(tmp_path, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="private-provider-error",
            headers={"Location": "https://untrusted.example/steal"},
        )

    app = create_app(
        data_dir=tmp_path,
        provider_resolver=private_resolver,
        provider_transport=httpx.MockTransport(handler),
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    app.state.database.update_voice_settings(**VOICE_SETTINGS)
    with TestClient(app) as client:
        analysis_id = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Offline probe"}
        ).json()["id"]
        response = client.post(f"/api/analyses/{analysis_id}/narration")
        assert response.status_code == 502
        assert "private-provider-error" not in response.text
        assert client.get("/api/history").json()[0]["narration_url"] is None
    assert len(calls) == 1
    assert calls[0].headers["host"] == "voicebox.home.arpa:17600"


def test_local_cpu_speech_has_long_budget_without_extending_discovery():
    clock = [0.0]

    def handler(request):
        if request.method == "GET":
            assert request.extensions["timeout"]["read"] <= 15.0
            return httpx.Response(200, json=[])
        assert request.extensions["timeout"]["read"] == 600.0
        assert request.extensions["timeout"]["connect"] == 5.0
        assert request.extensions["timeout"]["write"] == 15.0
        clock[0] += 180.0
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/mpeg"},
            content=b"ID3-long-local-review",
        )

    provider = ProviderClient(
        resolver=private_resolver,
        transport=httpx.MockTransport(handler),
        voicebox_host_allowlist={"voicebox.home.arpa"},
        monotonic=lambda: clock[0],
    )
    try:
        assert (
            provider.synthesize_speech(
                provider="voicebox",
                base_url=VOICEBOX,
                api_key=None,
                voice_id="hamish-profile",
                text="A full local review.",
                model_id="luxtts",
                output_format="mp3",
            )
            == b"ID3-long-local-review"
        )
        assert provider.discover_voicebox_voices(VOICEBOX) == []
    finally:
        provider.close()
