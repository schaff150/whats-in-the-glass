"""Model completions get their own timeout and reasoning-effort controls.

Regression: Gemini Flash models with default thinking took ~16 s before sending
the first response byte, but completions shared the 15 s metadata read limit,
so every analysis failed with a sanitized 502 while the connection test (a
quick model list) still passed.
"""

import json
import socket

import httpx
import pytest

from app.main import create_app
from app.providers import ProviderClient, ProviderError


def public_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def completion(content='{"ok": true}'):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def complete(client):
    return client.chat_completion(
        base_url="https://models.example/v1",
        api_key="key-canary",
        model_id="model-1",
        messages=[{"role": "user", "content": "hi"}],
    )


def test_completion_survives_a_slow_first_byte_beyond_the_metadata_read_limit():
    clock = [0.0]
    seen = []

    def handler(request):
        timeout = request.extensions["timeout"]
        seen.append((request.url.path, timeout["read"]))
        if request.url.path.endswith("/chat/completions"):
            # Reproduce Gemini thinking for 16 s before the response headers.
            if timeout["read"] < 16.0:
                raise httpx.ReadTimeout("still thinking", request=request)
            clock[0] += 16.0
            return completion()
        assert timeout["read"] <= 15.0
        return httpx.Response(200, json={"data": []})

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        monotonic=lambda: clock[0],
    )
    try:
        assert complete(client) == '{"ok": true}'
        # Discovery keeps the short metadata limits.
        assert client.discover_models("https://models.example/v1", "key") == []
    finally:
        client.close()
    assert seen[0] == ("/v1/chat/completions", 90.0)
    assert seen[1][1] <= 15.0


def test_completion_deadline_is_configurable_and_still_enforced():
    def handler(request):
        assert request.extensions["timeout"]["read"] == 30.0
        raise httpx.ReadTimeout("model hung", request=request)

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        analysis_total_timeout=30.0,
    )
    try:
        with pytest.raises(ProviderError, match="timed out"):
            complete(client)
    finally:
        client.close()


def test_completion_sends_configured_reasoning_effort():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return completion()

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        reasoning_effort=" Low ",
    )
    try:
        complete(client)
    finally:
        client.close()
    assert bodies[0]["reasoning_effort"] == "low"
    assert bodies[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("value", [None, "", "off", "OFF", "turbo"])
def test_unset_or_unknown_reasoning_effort_is_omitted(value):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return completion()

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        reasoning_effort=value,
    )
    try:
        complete(client)
    finally:
        client.close()
    assert "reasoning_effort" not in bodies[0]


def test_rejected_reasoning_effort_is_retried_once_without_it():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(
                400, json={"error": {"message": "Thinking level is not supported"}}
            )
        return completion()

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        reasoning_effort="minimal",
    )
    try:
        assert complete(client) == '{"ok": true}'
    finally:
        client.close()
    assert len(bodies) == 2
    assert bodies[0]["reasoning_effort"] == "minimal"
    assert "reasoning_effort" not in bodies[1]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_other_provider_errors_are_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "body-canary"}})

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        reasoning_effort="low",
    )
    try:
        with pytest.raises(ProviderError) as raised:
            complete(client)
    finally:
        client.close()
    assert len(calls) == 1
    assert str(raised.value) == f"The provider returned HTTP {status}."
    assert "body-canary" not in str(raised.value)


def test_app_defaults_to_low_effort_and_90_second_analysis_timeout(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("WITG_MODEL_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("WITG_ANALYSIS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("WITG_PROVIDER_TOTAL_TIMEOUT_SECONDS", raising=False)
    app = create_app(data_dir=tmp_path, secret_key=b"k" * 32)
    providers = app.state.provider_client
    try:
        assert providers._reasoning_effort == "low"
        assert providers._analysis_total_timeout == 90.0
        assert providers._total_timeout == 20.0
    finally:
        providers.close()


def test_app_reads_analysis_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("WITG_MODEL_REASONING_EFFORT", "none")
    monkeypatch.setenv("WITG_ANALYSIS_TIMEOUT_SECONDS", "45")
    app = create_app(data_dir=tmp_path, secret_key=b"k" * 32)
    providers = app.state.provider_client
    try:
        assert providers._reasoning_effort == "none"
        assert providers._analysis_total_timeout == 45.0
    finally:
        providers.close()


def test_app_can_disable_reasoning_effort_and_rejects_out_of_range_timeout(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WITG_MODEL_REASONING_EFFORT", "")
    monkeypatch.setenv("WITG_ANALYSIS_TIMEOUT_SECONDS", "9999")
    app = create_app(data_dir=tmp_path, secret_key=b"k" * 32)
    providers = app.state.provider_client
    try:
        assert providers._reasoning_effort is None
        assert providers._analysis_total_timeout == 90.0
    finally:
        providers.close()


def test_effort_retry_uses_the_remaining_deadline_not_a_fresh_one():
    clock = [0.0]
    reads = []

    def handler(request):
        body = json.loads(request.content)
        reads.append(request.extensions["timeout"]["read"])
        if "reasoning_effort" in body:
            clock[0] += 30.0
            return httpx.Response(400, json={"error": {"message": "nope"}})
        return completion()

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        reasoning_effort="low",
        monotonic=lambda: clock[0],
    )
    try:
        complete(client)
    finally:
        client.close()
    assert reads == [90.0, 60.0]


def test_a_retry_that_also_fails_surfaces_the_400_after_exactly_two_calls():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "bad image body-canary"}})

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        reasoning_effort="low",
    )
    try:
        with pytest.raises(ProviderError) as raised:
            complete(client)
        # A genuine 400 is not remembered as an effort rejection.
        with pytest.raises(ProviderError):
            complete(client)
    finally:
        client.close()
    assert str(raised.value) == "The provider returned HTTP 400."
    assert "body-canary" not in str(raised.value)
    assert len(calls) == 4
    assert [("reasoning_effort" in c) for c in calls] == [True, False, True, False]


def test_400_without_configured_effort_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={})

    client = ProviderClient(
        transport=httpx.MockTransport(handler), resolver=public_resolver
    )
    try:
        with pytest.raises(ProviderError, match="HTTP 400"):
            complete(client)
    finally:
        client.close()
    assert len(calls) == 1


def test_a_rejected_effort_is_remembered_per_model_and_logged(caplog):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body and body["model"] == "model-1":
            return httpx.Response(400, json={})
        return completion()

    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        reasoning_effort="low",
    )
    try:
        with caplog.at_level("WARNING", logger="app.providers"):
            complete(client)
        complete(client)
        client.chat_completion(
            base_url="https://models.example/v1",
            api_key="key-canary",
            model_id="model-2",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        client.close()
    assert [("reasoning_effort" in b) for b in bodies] == [True, False, False, True]
    assert "rejected reasoning_effort" in caplog.text
    assert "key-canary" not in caplog.text


def test_unknown_effort_is_logged_at_startup(caplog):
    with caplog.at_level("WARNING", logger="app.providers"):
        client = ProviderClient(resolver=public_resolver, reasoning_effort="lo")
    client.close()
    assert "Ignoring unknown model reasoning effort 'lo'" in caplog.text


def test_analysis_repair_retry_shares_one_deadline(tmp_path):
    from app.db import Database
    from app.services.analysis import ConfiguredAnalysisService

    clock = [0.0]
    reads = []
    good = {
        "identified_title": "Bottle",
        "region": "Islay",
        "age_statement": "16 years",
        "abv": "43%",
        "style": "Single malt",
        "overview": " ".join(["overview"] * 55),
        "context": " ".join(["context"] * 75),
        "tasting_notes": " ".join(["tasting"] * 55),
        "sources": [],
    }

    def handler(request):
        reads.append(request.extensions["timeout"]["read"])
        if len(reads) == 1:
            clock[0] += 50.0
            return completion("not json")
        return completion(json.dumps(good))

    database = Database(tmp_path / "witg.sqlite3", secret_key=b"k" * 32)
    database.initialize()
    database.update_model_settings(
        display_name="House",
        base_url="https://models.example/v1",
        model_id="model-1",
        api_key="key-canary",
    )
    client = ProviderClient(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        monotonic=lambda: clock[0],
    )
    try:
        result = ConfiguredAnalysisService(database, client).analyze(
            "Me", "Bottle", None
        )
    finally:
        client.close()
    assert result.identified_title == "Bottle"
    assert reads == [90.0, 40.0]
