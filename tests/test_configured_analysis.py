import json
import socket

import httpx
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.providers import ProviderClient
from app.services.analysis import ConfiguredAnalysisService, PhotoInput


def public_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def configured_database(tmp_path):
    database = Database(tmp_path / "witg.sqlite3", secret_key=b"k" * 32)
    database.initialize()
    database.update_model_settings(
        display_name="House model",
        base_url="https://models.example/v1",
        model_id="vision-model",
        api_key="analysis-key-canary",
    )
    return database


def test_configured_text_analysis_uses_chat_completions_and_never_fabricates_sources(
    tmp_path,
):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "identified_title": "Verified bottle",
                                    "region": "Speyside",
                                    "age_statement": "12 years",
                                    "abv": "46%",
                                    "style": "Single malt",
                                    "overview": "A concise overview.",
                                    "context": "A concise context.",
                                    "tasting_notes": "Pear and malt.",
                                    "sources": "Model supplied an unverified source",
                                }
                            )
                        }
                    }
                ]
            },
        )

    providers = ProviderClient(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )
    service = ConfiguredAnalysisService(configured_database(tmp_path), providers)
    result = service.analyze("Known bottle", photo=None)

    assert result.identified_title == "Verified bottle"
    assert result.sources == []
    request = seen["request"]
    assert request.url == "https://93.184.216.34/v1/chat/completions"
    assert request.headers["host"] == "models.example"
    assert request.extensions["sni_hostname"] == "models.example"
    assert request.headers["authorization"] == "Bearer analysis-key-canary"
    payload = json.loads(request.content)
    assert payload["model"] == "vision-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert "Known bottle" in str(payload["messages"])


def test_configured_photo_analysis_sends_a_data_uri_to_compatible_vision_model(
    tmp_path,
):
    seen = {}
    valid = {
        "identified_title": "Photo bottle",
        "region": "Unknown",
        "age_statement": "Unknown",
        "abv": "Unknown",
        "style": "Spirit",
        "overview": "Overview.",
        "context": "Context.",
        "tasting_notes": "Notes.",
        "sources": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(valid)}}]}
        )

    service = ConfiguredAnalysisService(
        configured_database(tmp_path),
        ProviderClient(
            transport=httpx.MockTransport(handler), resolver=public_resolver
        ),
    )
    result = service.analyze(
        None,
        PhotoInput(
            filename="label.png",
            content_type="image/png",
            content=b"\x89PNG\r\n\x1a\nimage",
        ),
    )

    user_content = seen["payload"]["messages"][1]["content"]
    assert result.identified_title == "Photo bottle"
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_app_uses_configured_model_for_analysis_when_no_replacement_is_injected(
    tmp_path,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "identified_title": "Runtime configured result",
                                    "region": "Highlands",
                                    "age_statement": "NAS",
                                    "abv": "40%",
                                    "style": "Blended whisky",
                                    "overview": "Overview.",
                                    "context": "Context.",
                                    "tasting_notes": "Tasting.",
                                    "sources": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    app = create_app(
        data_dir=tmp_path,
        secret_key=b"k" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_model_settings(
        display_name="Configured",
        base_url="https://models.example/v1",
        model_id="model-1",
        api_key="runtime-key-canary",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Bottle"}
        )

    assert response.status_code == 201
    assert response.json()["identified_title"] == "Runtime configured result"


def test_malformed_model_output_returns_sanitized_502_without_saving(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "not valid json secret-body-canary"}}
                ]
            },
        )

    app = create_app(
        data_dir=tmp_path,
        secret_key=b"k" * 32,
        provider_transport=httpx.MockTransport(handler),
        provider_resolver=public_resolver,
    )
    app.state.database.update_model_settings(
        display_name="Configured",
        base_url="https://models.example/v1",
        model_id="model-1",
        api_key="malformed-key-canary",
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/analyses", data={"drinker": "Me", "drink_name": "Bottle"}
        )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The configured model could not produce a valid analysis."
    }
    assert "malformed-key-canary" not in response.text
    assert "secret-body-canary" not in response.text
    assert app.state.database.list_analyses() == []
