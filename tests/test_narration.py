import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from app.db import Database
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


def test_saved_narration_is_restored_from_history_after_app_restart(tmp_path):
    tts_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tts_calls
        tts_calls += 1
        return httpx.Response(200, content=b"ID3-restored-narration")

    app = create_app(
        data_dir=tmp_path,
        secret_key=b"r" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="hamish-voice",
        voice_name="Hamish",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="restored-narration-key",
    )

    with TestClient(app) as client:
        analysis = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Saved pour"}
        ).json()
        generated_url = client.post(f"/api/analyses/{analysis['id']}/narration").json()[
            "audio_url"
        ]

    restarted = create_app(
        data_dir=tmp_path,
        secret_key=b"r" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    with TestClient(restarted) as client:
        restored_url = client.get("/api/history").json()[0]["narration_url"]
        audio = client.get(restored_url)

    assert restored_url == generated_url
    assert audio.status_code == 200
    assert audio.content == b"ID3-restored-narration"
    assert tts_calls == 1


def test_history_restores_the_most_recently_used_cached_narration(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ID3-voice-version")

    app = create_app(
        data_dir=tmp_path,
        secret_key=b"u" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="voice-a",
        voice_name="Voice A",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="recently-used-key",
    )

    with TestClient(app) as client:
        analysis_id = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Changing voices"}
        ).json()["id"]
        voice_a_url = client.post(f"/api/analyses/{analysis_id}/narration").json()[
            "audio_url"
        ]
        app.state.database.update_voice_settings(
            base_url="https://api.elevenlabs.io",
            voice_id="voice-b",
            voice_name="Voice B",
            tts_model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        voice_b_url = client.post(f"/api/analyses/{analysis_id}/narration").json()[
            "audio_url"
        ]
        app.state.database.update_voice_settings(
            base_url="https://api.elevenlabs.io",
            voice_id="voice-a",
            voice_name="Voice A",
            tts_model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        reused_voice_a_url = client.post(
            f"/api/analyses/{analysis_id}/narration"
        ).json()["audio_url"]

    assert voice_a_url != voice_b_url
    assert reused_voice_a_url == voice_a_url

    restarted = create_app(data_dir=tmp_path, secret_key=b"u" * 32)
    with TestClient(restarted) as client:
        restored_url = client.get("/api/history").json()[0]["narration_url"]

    assert restored_url == voice_a_url


def test_initialize_backfills_last_used_time_for_existing_narrations(tmp_path):
    path = tmp_path / "witg.sqlite3"
    created_at = "2026-01-02T03:04:05+00:00"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE narrations (
                token TEXT PRIMARY KEY,
                analysis_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                file_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (analysis_id, fingerprint)
            )
            """
        )
        connection.execute(
            "INSERT INTO narrations VALUES (?, ?, ?, ?, ?)",
            ("a" * 64, 1, "fingerprint", "cached.mp3", created_at),
        )

    Database(path).initialize()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(narrations)")
        }
        last_used_at = connection.execute(
            "SELECT last_used_at FROM narrations WHERE token = ?", ("a" * 64,)
        ).fetchone()[0]

    assert "last_used_at" in columns
    assert last_used_at == created_at
