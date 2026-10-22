import asyncio
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers import ProviderClient, ProviderError


class SyncOnlyTransport(httpx.BaseTransport):
    """Unlike MockTransport, this implements no async transport interface."""

    def __init__(self):
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        assert request.method == "GET", "Speech must never use blocking sync I/O"
        return httpx.Response(200, json={"data": [{"id": "metadata-model"}]})


class AsyncOnlySpeechTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.close_count = 0

    async def handle_async_request(self, request):
        self.requests.append(request)
        return await self.handler(request)

    async def aclose(self):
        self.close_count += 1


def safe_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    address = "192.168.0.87" if host == "voicebox.home.arpa" else "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


def speech_options(provider):
    elevenlabs = provider == "elevenlabs"
    return {
        "provider": provider,
        "base_url": (
            "https://api.elevenlabs.io"
            if elevenlabs
            else "http://voicebox.home.arpa:17600"
        ),
        "api_key": "transport-key-canary" if elevenlabs else None,
        "voice_id": "hamish-profile",
        "text": "A bounded narration.",
        "model_id": "eleven_multilingual_v2" if elevenlabs else "luxtts",
        "output_format": "mp3_44100_128" if elevenlabs else "mp3",
    }


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    calls = []

    def unexpected_network(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Injected transports must never use real networking")

    # Block HTTP/DNS egress, not Windows asyncio's loopback socketpair setup.
    monkeypatch.setattr(socket, "getaddrinfo", unexpected_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unexpected_network)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", unexpected_network
    )
    yield
    assert calls == []


@pytest.mark.parametrize("provider", ["elevenlabs", "voicebox"])
def test_sync_only_transport_keeps_metadata_but_fails_speech_closed(provider):
    transport = SyncOnlyTransport()
    assert not isinstance(transport, httpx.AsyncBaseTransport)
    client = ProviderClient(
        transport=transport,
        resolver=safe_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
        max_concurrency=1,
        capacity_wait_timeout=0.01,
    )
    try:
        assert client.discover_models("https://models.example/v1", None) == [
            {"id": "metadata-model"}
        ]
        with pytest.raises(ProviderError, match="async.*transport") as caught:
            client.synthesize_speech(**speech_options(provider))
        assert "transport-key-canary" not in str(caught.value)
        assert "__aenter__" not in str(caught.value)
        # Metadata still succeeds after rejection and proves capacity is free.
        assert client.discover_models("https://models.example/v1", None) == [
            {"id": "metadata-model"}
        ]
        assert len(transport.requests) == 2
    finally:
        client.close()


@pytest.mark.parametrize("provider", ["elevenlabs", "voicebox"])
@pytest.mark.parametrize(
    ("route", "detail"),
    [
        ("preview", "Voice preview failed."),
        ("narration", "Narration could not be generated."),
    ],
)
def test_sync_only_speech_transport_api_errors_are_sanitized(
    tmp_path, provider, route, detail
):
    transport = SyncOnlyTransport()
    options = speech_options(provider)
    app = create_app(
        data_dir=tmp_path,
        secret_key=b"t" * 32,
        provider_transport=transport,
        provider_resolver=safe_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    app.state.database.update_voice_settings(
        provider=provider,
        base_url=options["base_url"],
        voice_id=options["voice_id"],
        voice_name="Hamish",
        tts_model_id=options["model_id"],
        output_format=options["output_format"],
        api_key=options["api_key"],
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            if route == "preview":
                response = client.post(
                    "/api/settings/voice/preview",
                    json={"text": "A safe preview."},
                    headers={"Origin": "http://testserver"},
                )
            else:
                analysis_id = client.post(
                    "/api/analyses", data={"drinker": "Me", "drink_name": "Pour"}
                ).json()["id"]
                response = client.post(f"/api/analyses/{analysis_id}/narration")
                assert client.get("/api/history").json()[0]["narration_url"] is None
            assert response.status_code == 502, response.text
            assert response.json() == {"detail": detail}
            assert "transport-key-canary" not in response.text
            assert "AttributeError" not in response.text
            assert "__aenter__" not in response.text
        assert transport.requests == []
        assert not (tmp_path / "narration").exists()
    finally:
        app.state.provider_client.close()


@pytest.mark.parametrize("provider", ["elevenlabs", "voicebox"])
@pytest.mark.parametrize("dual_metadata_transport", [False, True])
def test_explicit_async_speech_transport_is_separate_and_takes_precedence(
    provider, dual_metadata_transport
):
    metadata = SyncOnlyTransport()

    async def speech_handler(request):
        return httpx.Response(
            200, headers={"Content-Type": "audio/mpeg"}, content=b"ID3-async-speech"
        )

    speech = AsyncOnlySpeechTransport(speech_handler)
    assert not isinstance(speech, httpx.BaseTransport)
    client = ProviderClient(
        transport=(
            httpx.MockTransport(metadata.handle_request)
            if dual_metadata_transport
            else metadata
        ),
        speech_transport=speech,
        resolver=safe_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
        monotonic=lambda: 0.0,
    )
    try:
        assert client.discover_models("https://models.example/v1", None) == [
            {"id": "metadata-model"}
        ]
        for _ in range(2):
            assert (
                client.synthesize_speech(**speech_options(provider))
                == b"ID3-async-speech"
            )
        assert len(metadata.requests) == 1
        assert len(speech.requests) == 2
        assert speech.close_count == 2
        request = speech.requests[0]
        assert request.method == "POST"
        assert request.headers["connection"] == "close"
        assert request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 120.0 if provider == "elevenlabs" else 600.0,
            "write": 15.0,
            "pool": 5.0,
        }
        if provider == "elevenlabs":
            assert request.url.host == "93.184.216.34"
            assert request.headers["host"] == "api.elevenlabs.io"
            assert request.extensions["sni_hostname"] == "api.elevenlabs.io"
            assert request.headers["xi-api-key"] == "transport-key-canary"
        else:
            assert request.url.host == "192.168.0.87"
            assert request.headers["host"] == "voicebox.home.arpa:17600"
            assert "xi-api-key" not in request.headers
            assert "authorization" not in request.headers
    finally:
        client.close()


@pytest.mark.parametrize("provider", ["elevenlabs", "voicebox"])
@pytest.mark.parametrize("phase", ["headers", "body"])
def test_explicit_async_speech_transport_is_cancelled_and_closed(provider, phase):
    metadata = SyncOnlyTransport()
    state = {"entered": False, "cancelled": False, "stream_closed": False}

    async def block():
        state["entered"] = True
        try:
            # Bounded even if a regression removes the speech cancellation.
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    class BlockingAudio(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"ID3"
            await block()
            yield b"-late-audio"

        async def aclose(self):
            state["stream_closed"] = True

    async def speech_handler(request):
        if phase == "headers":
            await block()
        return httpx.Response(
            200, headers={"Content-Type": "audio/mpeg"}, stream=BlockingAudio()
        )

    speech = AsyncOnlySpeechTransport(speech_handler)
    client = ProviderClient(
        transport=metadata,
        speech_transport=speech,
        resolver=safe_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
        speech_total_timeout=0.1,
        voicebox_speech_total_timeout=0.1,
        max_concurrency=1,
        capacity_wait_timeout=0.01,
    )
    try:
        started = time.monotonic()
        prefix = "ElevenLabs" if provider == "elevenlabs" else "Voicebox"
        with pytest.raises(ProviderError, match=rf"^{prefix} timed out\.$") as caught:
            client.synthesize_speech(**speech_options(provider))
        assert time.monotonic() - started < 0.5
        assert state["entered"] and state["cancelled"]
        assert state["stream_closed"] is (phase == "body")
        assert speech.close_count == 1
        assert len(speech.requests) == 1
        assert "transport-key-canary" not in str(caught.value)
        # Cancellation closes the async transport before capacity is reused.
        assert client.discover_models("https://models.example/v1", None) == [
            {"id": "metadata-model"}
        ]
    finally:
        client.close()


@pytest.mark.parametrize("provider", ["elevenlabs", "voicebox"])
@pytest.mark.parametrize("status", [200, 503])
def test_create_app_routes_speech_through_explicit_async_transport(
    tmp_path, provider, status
):
    metadata = SyncOnlyTransport()

    async def speech_handler(request):
        if status != 200:
            return httpx.Response(
                status, text="raw-provider-secret transport-key-canary"
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "audio/mpeg"},
            content=b"ID3-explicit-app-audio",
        )

    speech = AsyncOnlySpeechTransport(speech_handler)
    options = speech_options(provider)
    app = create_app(
        data_dir=tmp_path,
        secret_key=b"t" * 32,
        provider_transport=metadata,
        provider_speech_transport=speech,
        provider_resolver=safe_resolver,
        voicebox_host_allowlist={"voicebox.home.arpa"},
    )
    app.state.database.update_voice_settings(
        provider=provider,
        base_url=options["base_url"],
        voice_id=options["voice_id"],
        voice_name="Hamish",
        tts_model_id=options["model_id"],
        output_format=options["output_format"],
        api_key=options["api_key"],
    )
    try:
        with TestClient(app) as client:
            preview = client.post(
                "/api/settings/voice/preview",
                json={"text": "An async preview."},
                headers={"Origin": "http://testserver"},
            )
            analysis_id = client.post(
                "/api/analyses", data={"drinker": "Me", "drink_name": "Async pour"}
            ).json()["id"]
            narration = client.post(f"/api/analyses/{analysis_id}/narration")
            if status == 200:
                assert preview.status_code == 200, preview.text
                assert preview.content == b"ID3-explicit-app-audio"
                assert narration.status_code == 200, narration.text
                assert (
                    client.get(narration.json()["audio_url"]).content
                    == b"ID3-explicit-app-audio"
                )
            else:
                assert preview.status_code == narration.status_code == 502
                assert preview.json() == {"detail": "Voice preview failed."}
                assert narration.json() == {
                    "detail": "Narration could not be generated."
                }
                assert client.get("/api/history").json()[0]["narration_url"] is None
                assert not (tmp_path / "narration").exists()
            for response in (preview, narration):
                assert "transport-key-canary" not in response.text
                assert "raw-provider-secret" not in response.text
        assert metadata.requests == []
        assert len(speech.requests) == 2
        assert speech.close_count == 2
    finally:
        app.state.provider_client.close()
