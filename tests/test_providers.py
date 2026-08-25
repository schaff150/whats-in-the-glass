import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.providers import (
    ProviderCapacityError,
    ProviderClient,
    ProviderError,
    ProviderRateLimitError,
)


def public_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_model_discovery_uses_bearer_auth_and_safe_model_ids():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "vision-model", "owned_by": "provider", "secret": "omit"},
                    {"id": "text-model"},
                    {"not_id": "ignored"},
                ]
            },
        )

    client = ProviderClient(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )
    models = client.discover_models("https://models.example/v1", "discovery-key-canary")

    assert models == [{"id": "text-model"}, {"id": "vision-model"}]
    assert seen["request"].url == "https://93.184.216.34/v1/models"
    assert seen["request"].headers["host"] == "models.example"
    assert seen["request"].extensions["sni_hostname"] == "models.example"
    assert seen["request"].headers["authorization"] == "Bearer discovery-key-canary"
    assert "discovery-key-canary" not in json.dumps(models)


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example/v1",
        "https://user:pass@models.example/v1",
        "https://models.example/v1?key=secret",
        "https://models.example/v1#fragment",
    ],
)
def test_model_discovery_rejects_unsafe_base_urls(url):
    client = ProviderClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        resolver=public_resolver,
    )

    with pytest.raises(ProviderError, match="endpoint"):
        client.discover_models(url, None)


def test_private_model_destination_requires_exact_operator_allowlist():
    def private_resolver(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.25", port))]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "lan-model"}]})
    )
    blocked = ProviderClient(transport=transport, resolver=private_resolver)
    allowed = ProviderClient(
        transport=transport,
        resolver=private_resolver,
        model_host_allowlist={"modelbox.home.arpa"},
    )

    with pytest.raises(ProviderError, match="not allowed"):
        blocked.discover_models("https://modelbox.home.arpa/v1", None)
    assert allowed.discover_models("https://modelbox.home.arpa/v1", None) == [
        {"id": "lan-model"}
    ]


def test_allowlist_never_permits_metadata_or_other_forbidden_address_classes():
    def metadata_resolver(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    client = ProviderClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        resolver=metadata_resolver,
        model_host_allowlist={"modelbox.home.arpa"},
    )

    with pytest.raises(ProviderError, match="not allowed"):
        client.discover_models("http://modelbox.home.arpa/v1", None)


def test_validated_ip_is_pinned_through_request_with_original_host_and_tls_sni():
    resolutions = ["93.184.216.34", "10.0.0.25"]
    seen = {}

    def rebinding_resolver(host, port, *, type):
        address = resolutions.pop(0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json={"data": []})

    client = ProviderClient(
        transport=httpx.MockTransport(handler), resolver=rebinding_resolver
    )
    client.discover_models("https://models.example/v1", None)

    request = seen["request"]
    assert request.url == "https://93.184.216.34/v1/models"
    assert request.headers["host"] == "models.example"
    assert request.headers["connection"] == "close"
    assert request.extensions["sni_hostname"] == "models.example"
    assert resolutions == ["10.0.0.25"]


def test_http_model_endpoint_requires_exact_allowlisted_private_host():
    def private_resolver(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.25", port))]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "lan"}]})
    )
    allowed = ProviderClient(
        transport=transport,
        resolver=private_resolver,
        model_host_allowlist={"modelbox.home.arpa"},
    )
    blocked = ProviderClient(transport=transport, resolver=private_resolver)

    assert allowed.discover_models("http://modelbox.home.arpa:8080/v1", None) == [
        {"id": "lan"}
    ]
    with pytest.raises(ProviderError, match="not allowed"):
        blocked.discover_models("http://modelbox.home.arpa:8080/v1", None)


class DripStream(httpx.SyncByteStream):
    def __init__(self, clock):
        self.clock = clock

    def __iter__(self):
        yield b'{"data":'
        self.clock[0] += 0.6
        yield b"[]"
        self.clock[0] += 0.6
        yield b"}"


def test_total_deadline_stops_a_continuously_dripping_response():
    clock = [100.0]
    client = ProviderClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=DripStream(clock))
        ),
        resolver=public_resolver,
        total_timeout=1.0,
        monotonic=lambda: clock[0],
    )

    with pytest.raises(ProviderError, match="timed out"):
        client.discover_models("https://models.example/v1", None)


def test_total_deadline_covers_dns_resolution():
    def slow_resolver(host, port, *, type):
        time.sleep(0.05)
        return public_resolver(host, port, type=type)

    client = ProviderClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        resolver=slow_resolver,
        total_timeout=0.01,
    )

    with pytest.raises(ProviderError, match="timed out"):
        client.discover_models("https://models.example/v1", None)


def test_elevenlabs_voice_discovery_paginates_on_has_more_and_maps_safe_fields():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("next_page_token") == "page-two":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "voice_id": "v2",
                            "name": "Zulu",
                            "fine_tuning": {"secret": "omit"},
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "v1",
                        "name": "Alpha",
                        "category": "premade",
                        "description": "Warm",
                        "preview_url": "https://cdn.example/preview.mp3",
                        "labels": {"accent": "Scottish"},
                        "settings": {"secret": "omit"},
                    }
                ],
                "has_more": True,
                "next_page_token": "page-two",
            },
        )

    client = ProviderClient(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )
    voices = client.discover_voices("https://api.elevenlabs.io", "voice-key-canary")

    assert [voice["voice_id"] for voice in voices] == ["v1", "v2"]
    assert voices[0] == {
        "voice_id": "v1",
        "name": "Alpha",
        "category": "premade",
        "description": "Warm",
        "preview_url": "https://cdn.example/preview.mp3",
        "labels": {"accent": "Scottish"},
    }
    assert len(requests) == 2
    assert requests[0].headers["xi-api-key"] == "voice-key-canary"
    assert requests[0].url.params["page_size"] == "100"
    assert requests[0].url.params["sort"] == "name"
    assert requests[0].url.params["include_total_count"] == "false"
    assert requests[1].url.params["next_page_token"] == "page-two"


def test_elevenlabs_voice_discovery_deduplicates_voice_ids_across_pages():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "voices": [{"voice_id": "same", "name": "First"}],
                    "has_more": True,
                    "next_page_token": "next",
                },
            )
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"voice_id": "same", "name": "Duplicate"},
                    {"voice_id": "other", "name": "Other"},
                ],
                "has_more": False,
            },
        )

    client = ProviderClient(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )

    assert client.discover_voices("https://api.elevenlabs.io", "key") == [
        {"voice_id": "same", "name": "First"},
        {"voice_id": "other", "name": "Other"},
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503, text="provider-secret"), "HTTP 503"),
        (httpx.Response(200, content=b"x" * (1024 * 1024 + 1)), "too large"),
    ],
)
def test_provider_json_errors_are_explicit_bounded_and_sanitized(response, message):
    client = ProviderClient(
        transport=httpx.MockTransport(lambda request: response),
        resolver=public_resolver,
    )

    with pytest.raises(ProviderError, match=message) as caught:
        client.discover_models("https://models.example/v1", "secret-key")

    assert "provider-secret" not in str(caught.value)
    assert "secret-key" not in str(caught.value)


def test_provider_inactivity_timeout_is_sanitized():
    def timeout(request):
        raise httpx.ReadTimeout("raw-timeout-secret", request=request)

    client = ProviderClient(
        transport=httpx.MockTransport(timeout), resolver=public_resolver
    )

    with pytest.raises(ProviderError, match="timed out") as caught:
        client.discover_models("https://models.example/v1", "timeout-key")

    assert "raw-timeout-secret" not in str(caught.value)


def test_global_provider_concurrency_limit_fails_closed_without_extra_call():
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2)
        return httpx.Response(200, json={"data": []})

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        max_concurrency=1,
        capacity_wait_timeout=0.01,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.discover_models, "https://models.example/v1", None
        )
        assert entered.wait(1)
        try:
            with pytest.raises(ProviderCapacityError, match="busy"):
                client.discover_models("https://models.example/v1", None)
        finally:
            release.set()
        assert future.result(timeout=2) == []

    assert calls == 1


def test_operator_provider_request_budget_resets_only_with_window():
    clock = [10.0]
    client = ProviderClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []})
        ),
        resolver=public_resolver,
        request_budget=1,
        request_budget_window=60.0,
        monotonic=lambda: clock[0],
    )

    client.discover_models("https://models.example/v1", None)
    with pytest.raises(ProviderRateLimitError, match="budget"):
        client.discover_models("https://models.example/v1", None)
    clock[0] += 61
    assert client.discover_models("https://models.example/v1", None) == []


def test_elevenlabs_tts_uses_selected_voice_model_and_output_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, content=b"ID3-safe-audio")

    client = ProviderClient(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )
    audio = client.synthesize_speech(
        base_url="https://api.elevenlabs.io",
        api_key="tts-key-canary",
        voice_id="voice-id",
        text="A short preview.",
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )

    request = seen["request"]
    assert audio == b"ID3-safe-audio"
    assert request.method == "POST"
    assert request.url.path == "/v1/text-to-speech/voice-id"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == "tts-key-canary"
    assert json.loads(request.content) == {
        "text": "A short preview.",
        "model_id": "eleven_multilingual_v2",
    }


def test_elevenlabs_model_discovery_keeps_only_text_to_speech_models():
    client = ProviderClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=[
                    {
                        "model_id": "eleven_multilingual_v2",
                        "name": "Multilingual v2",
                        "can_do_text_to_speech": True,
                        "can_do_voice_conversion": True,
                    },
                    {
                        "model_id": "voice-conversion-only",
                        "can_do_text_to_speech": False,
                    },
                ],
            )
        ),
        resolver=public_resolver,
    )

    models = client.discover_tts_models(
        "https://api.elevenlabs.io", "models-key-canary"
    )

    assert models == [{"model_id": "eleven_multilingual_v2", "name": "Multilingual v2"}]
