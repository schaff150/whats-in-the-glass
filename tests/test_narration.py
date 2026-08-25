import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from app.main import create_app


def public_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_saved_analysis_narration_is_generated_once_cached_and_safely_served(tmp_path):
    tts_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        tts_requests.append(request)
        return httpx.Response(200, content=b"ID3-narration-audio")

    app = create_app(
        data_dir=tmp_path,
        secret_key=b"n" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="hamish-voice",
        voice_name="Hamish",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="narration-key-canary",
    )

    with TestClient(app) as client:
        analysis = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "House pour"}
        ).json()
        first = client.post(f"/api/analyses/{analysis['id']}/narration")
        second = client.post(f"/api/analyses/{analysis['id']}/narration")
        app.state.database.update_voice_settings(
            base_url="https://api.elevenlabs.io",
            voice_id="second-voice",
            voice_name="Second voice",
            tts_model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
            api_key="",
        )
        regenerated = client.post(f"/api/analyses/{analysis['id']}/narration")
        audio = client.get(first.json()["audio_url"])
        traversal = client.get("/api/audio/../../witg.sqlite3")

    assert first.status_code == 200
    assert second.json() == first.json()
    assert regenerated.json() != first.json()
    assert len(tts_requests) == 2
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/mpeg")
    assert audio.content == b"ID3-narration-audio"
    assert traversal.status_code in {404, 422}
    assert "House pour" not in first.json()["audio_url"]


def test_concurrent_identical_narration_is_single_flight_per_fingerprint(tmp_path):
    calls = 0
    calls_lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return httpx.Response(200, content=b"ID3-single-flight")

    app = create_app(
        data_dir=tmp_path,
        secret_key=b"s" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="single-flight-voice",
        voice_name="Voice",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="single-flight-key",
    )
    with TestClient(app) as client:
        analysis_id = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Concurrent"}
        ).json()["id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(app.state.narration_service.generate, analysis_id)
            for _ in range(2)
        ]
        tokens = [future.result(timeout=2) for future in futures]

    assert tokens[0] == tokens[1]
    assert calls == 1
