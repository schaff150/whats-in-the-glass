import base64
import socket

import httpx
from fastapi.testclient import TestClient

from app.main import create_app

MASTER_KEY = base64.urlsafe_b64encode(b"test-provider-master-key-32bytes")


def public_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_public_analysis_has_conservative_per_client_rate_limit(tmp_path):
    app = create_app(data_dir=tmp_path, analysis_rate_limit=1, rate_limit_window=60.0)

    with TestClient(app) as client:
        first = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "First"}
        )
        limited = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Second"}
        )

    assert first.status_code == 201
    assert limited.status_code == 429
    assert limited.json() == {
        "detail": "Analysis rate limit exceeded. Try again later."
    }


def test_public_narration_has_independent_per_client_rate_limit(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=MASTER_KEY,
        narration_rate_limit=1,
        rate_limit_window=60.0,
        provider_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"ID3-limited")
        ),
        provider_resolver=public_resolver,
    )
    app.state.database.update_voice_settings(
        base_url="https://api.elevenlabs.io",
        voice_id="voice",
        voice_name="Voice",
        tts_model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        api_key="rate-limit-key",
    )

    with TestClient(app) as client:
        analysis = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Pour"}
        ).json()
        first = client.post(f"/api/analyses/{analysis['id']}/narration")
        limited = client.post(f"/api/analyses/{analysis['id']}/narration")

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json() == {
        "detail": "Narration rate limit exceeded. Try again later."
    }
