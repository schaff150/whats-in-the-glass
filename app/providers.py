import asyncio
import ipaddress
import json
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from app.models import ELEVENLABS_HOSTS, validate_base_url

MAX_JSON_BYTES = 1024 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_VOICE_PAGES = 10
_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class ProviderError(RuntimeError):
    """A sanitized provider failure safe to show to an administrator."""


class ProviderCapacityError(ProviderError):
    """The configured global provider concurrency is exhausted."""


class ProviderRateLimitError(ProviderError):
    """The configured in-process provider request budget is exhausted."""


@dataclass(frozen=True)
class _Destination:
    pinned_base_url: str
    host_header: str
    sni_hostname: str
    deadline: float


class ProviderClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        speech_transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
        model_host_allowlist: set[str] | None = None,
        voicebox_host_allowlist: set[str] | None = None,
        voicebox_speech_total_timeout: float = 600.0,
        total_timeout: float = 20.0,
        speech_total_timeout: float = 120.0,
        monotonic: Callable[[], float] = time.monotonic,
        max_concurrency: int = 4,
        capacity_wait_timeout: float = 1.0,
        request_budget: int | None = 120,
        request_budget_window: float = 60.0,
    ):
        self._resolver = resolver
        self._model_host_allowlist = {
            host.casefold() for host in (model_host_allowlist or set())
        }
        self._voicebox_host_allowlist = {
            host.casefold() for host in (voicebox_host_allowlist or set())
        }
        self._voicebox_speech_total_timeout = voicebox_speech_total_timeout
        self._total_timeout = total_timeout
        self._speech_total_timeout = speech_total_timeout
        self._monotonic = monotonic
        self._capacity = threading.BoundedSemaphore(max(1, max_concurrency))
        self._capacity_wait_timeout = max(0.0, capacity_wait_timeout)
        self._request_budget = (
            request_budget if request_budget and request_budget > 0 else None
        )
        self._request_budget_window = request_budget_window
        self._request_times: deque[float] = deque()
        self._request_times_lock = threading.Lock()
        self._dns_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="provider-dns"
        )
        self._transport = transport
        self._speech_transport = speech_transport
        # Preserve dual-interface MockTransport injection, never sync-only I/O.
        if speech_transport is None and isinstance(transport, httpx.AsyncBaseTransport):
            self._speech_transport = transport
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=5.0, read=15.0, write=15.0),
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()
        self._dns_executor.shutdown(wait=False, cancel_futures=True)

    def _timeout_error(self, *, elevenlabs: bool) -> ProviderError:
        return ProviderError(
            "ElevenLabs timed out." if elevenlabs else "The provider timed out."
        )

    def _remaining(self, deadline: float, *, elevenlabs: bool) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise self._timeout_error(elevenlabs=elevenlabs)
        return remaining

    def _validate_destination(
        self,
        base_url: str,
        *,
        elevenlabs: bool = False,
        voicebox: bool = False,
        total_timeout: float | None = None,
    ) -> _Destination:
        deadline = self._monotonic() + (
            self._total_timeout if total_timeout is None else total_timeout
        )
        try:
            clean = validate_base_url(
                base_url, official_hosts=ELEVENLABS_HOSTS if elevenlabs else None
            )
        except ValueError as error:
            raise ProviderError("The provider endpoint is not allowed.") from error
        parsed = urlsplit(clean)
        host = (parsed.hostname or "").casefold()
        if host in {"metadata.google.internal", "metadata", "instance-data"}:
            raise ProviderError("The provider endpoint is not allowed.")
        allowlist = (
            self._voicebox_host_allowlist if voicebox else self._model_host_allowlist
        )
        allowlisted = not elevenlabs and host in allowlist
        if voicebox and not allowlisted:
            raise ProviderError("The Voicebox endpoint is not allowed.")
        if parsed.scheme == "http" and not allowlisted:
            raise ProviderError("The provider endpoint is not allowed.")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as error:
            raise ProviderError("The provider endpoint is not allowed.") from error
        future = self._dns_executor.submit(
            self._resolver, host, port, type=socket.SOCK_STREAM
        )
        try:
            addresses = future.result(
                timeout=self._remaining(deadline, elevenlabs=elevenlabs)
            )
        except FutureTimeoutError as error:
            future.cancel()
            raise self._timeout_error(elevenlabs=elevenlabs) from error
        except OSError as error:
            raise ProviderError(
                "The provider endpoint could not be resolved."
            ) from error
        self._remaining(deadline, elevenlabs=elevenlabs)
        if not addresses:
            raise ProviderError("The provider endpoint could not be resolved.")

        validated: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address[4][0])
            except (ValueError, IndexError, TypeError) as error:
                raise ProviderError(
                    "The provider endpoint could not be resolved safely."
                ) from error
            always_forbidden = (
                ip in _METADATA_ADDRESSES
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or ip.is_reserved
            )
            if always_forbidden or (ip.is_private and not allowlisted):
                raise ProviderError("The provider endpoint is not allowed.")
            if (voicebox or parsed.scheme == "http") and not ip.is_private:
                raise ProviderError("The provider endpoint is not allowed.")
            validated.append(ip)

        pinned_ip = str(validated[0])
        pinned_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        default_port = 443 if parsed.scheme == "https" else 80
        pinned_netloc = f"{pinned_host}:{port}" if port != default_port else pinned_host
        original_host = f"[{host}]" if ":" in host else host
        host_header = (
            f"{original_host}:{port}" if port != default_port else original_host
        )
        pinned_base_url = urlunsplit(
            (parsed.scheme, pinned_netloc, parsed.path.rstrip("/"), "", "")
        )
        return _Destination(pinned_base_url, host_header, host, deadline)

    @staticmethod
    def _url(
        destination: _Destination,
        suffix: str,
        query: dict[str, str] | None = None,
    ) -> str:
        url = f"{destination.pinned_base_url}/{suffix.lstrip('/')}"
        return f"{url}?{urlencode(query)}" if query else url

    def _request_options(
        self,
        destination: _Destination,
        headers: dict[str, str] | None,
        *,
        elevenlabs: bool,
        read_timeout: float = 15.0,
    ) -> tuple[dict[str, str], httpx.Timeout, dict[str, Any]]:
        remaining = self._remaining(destination.deadline, elevenlabs=elevenlabs)
        request_headers = dict(headers or {})
        request_headers["Host"] = destination.host_header
        request_headers["Connection"] = "close"
        phase_timeout = min(remaining, 15.0)
        timeout = httpx.Timeout(
            remaining,
            connect=min(remaining, 5.0),
            read=min(remaining, read_timeout),
            write=phase_timeout,
            pool=min(remaining, 5.0),
        )
        extensions: dict[str, Any] = {}
        if destination.pinned_base_url.startswith("https://"):
            extensions["sni_hostname"] = destination.sni_hostname
        return request_headers, timeout, extensions

    @contextmanager
    def _provider_slot(
        self, *, deadline: float | None = None, elevenlabs: bool = False
    ) -> Iterator[None]:
        now = self._monotonic()
        with self._request_times_lock:
            while (
                self._request_times
                and now - self._request_times[0] >= self._request_budget_window
            ):
                self._request_times.popleft()
            if (
                self._request_budget is not None
                and len(self._request_times) >= self._request_budget
            ):
                raise ProviderRateLimitError(
                    "The provider request budget is temporarily exhausted."
                )
            self._request_times.append(now)
        wait_timeout = self._capacity_wait_timeout
        if deadline is not None:
            wait_timeout = min(
                wait_timeout, self._remaining(deadline, elevenlabs=elevenlabs)
            )
        if not self._capacity.acquire(timeout=wait_timeout):
            with self._request_times_lock:
                try:
                    self._request_times.remove(now)
                except ValueError:
                    pass
            if deadline is not None:
                self._remaining(deadline, elevenlabs=elevenlabs)
            raise ProviderCapacityError("The provider is busy. Try again shortly.")
        try:
            yield
        finally:
            self._capacity.release()

    def _json_request(
        self,
        destination: _Destination,
        method: str,
        suffix: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        elevenlabs: bool = False,
    ) -> Any:
        request_headers, timeout, extensions = self._request_options(
            destination, headers, elevenlabs=elevenlabs
        )
        content = bytearray()
        try:
            with (
                self._provider_slot(),
                self._client.stream(
                    method,
                    self._url(destination, suffix, query),
                    headers=request_headers,
                    json=body,
                    timeout=timeout,
                    extensions=extensions,
                ) as response,
            ):
                self._remaining(destination.deadline, elevenlabs=elevenlabs)
                if response.is_redirect:
                    raise ProviderError(
                        "The provider returned a redirect, which is not allowed."
                    )
                if response.status_code >= 400:
                    prefix = "ElevenLabs" if elevenlabs else "The provider"
                    raise ProviderError(
                        f"{prefix} returned HTTP {response.status_code}."
                    )
                for chunk in response.iter_bytes():
                    self._remaining(destination.deadline, elevenlabs=elevenlabs)
                    content.extend(chunk)
                    if len(content) > MAX_JSON_BYTES:
                        raise ProviderError("The provider response was too large.")
                self._remaining(destination.deadline, elevenlabs=elevenlabs)
        except httpx.TimeoutException as error:
            raise self._timeout_error(elevenlabs=elevenlabs) from error
        except httpx.HTTPError as error:
            message = (
                "ElevenLabs could not be reached."
                if elevenlabs
                else "The provider could not be reached."
            )
            raise ProviderError(message) from error
        try:
            return json.loads(content)
        except (ValueError, UnicodeDecodeError) as error:
            raise ProviderError("The provider returned invalid JSON.") from error

    def validate_model_endpoint(self, base_url: str) -> None:
        self._validate_destination(base_url)

    def validate_voicebox_endpoint(self, base_url: str) -> None:
        self._validate_destination(base_url, voicebox=True)

    def discover_models(
        self, base_url: str, api_key: str | None
    ) -> list[dict[str, str]]:
        destination = self._validate_destination(base_url)
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = self._json_request(destination, "GET", "models", headers=headers)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError("The provider returned an invalid model list.")
        ids = {
            item["id"]
            for item in payload["data"]
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and 0 < len(item["id"]) <= 200
        }
        return [{"id": model_id} for model_id in sorted(ids)]

    def chat_completion(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model_id: str,
        messages: list[dict[str, Any]],
    ) -> str:
        destination = self._validate_destination(base_url)
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = self._json_request(
            destination,
            "POST",
            "chat/completions",
            headers=headers,
            body={
                "model": model_id,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
        )
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("The model returned an invalid completion.") from error
        if not isinstance(content, str):
            raise ProviderError("The model returned an invalid completion.")
        return content

    def discover_voicebox_voices(self, base_url: str) -> list[dict[str, str]]:
        destination = self._validate_destination(base_url, voicebox=True)
        payload = self._json_request(destination, "GET", "profiles")
        if not isinstance(payload, list):
            raise ProviderError("Voicebox returned an invalid profile list.")
        voices = {
            item["id"]: {"voice_id": item["id"], "name": item["name"]}
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and 0 < len(item["id"]) <= 200
            and isinstance(item.get("name"), str)
            and 0 < len(item["name"]) <= 200
        }
        return sorted(
            voices.values(), key=lambda item: (item["name"], item["voice_id"])
        )

    def discover_voicebox_models(self, base_url: str) -> list[dict[str, str]]:
        destination = self._validate_destination(base_url, voicebox=True)
        payload = self._json_request(destination, "GET", "v1/models")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError("Voicebox returned an invalid model list.")
        models = {}
        for item in payload["data"]:
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and 0 < len(item["id"]) <= 200
            ):
                description = item.get("description")
                models[item["id"]] = {
                    "model_id": item["id"],
                    "name": description[:200]
                    if isinstance(description, str)
                    else item["id"],
                }
        return sorted(models.values(), key=lambda item: item["model_id"])

    def discover_voices(self, base_url: str, api_key: str) -> list[dict[str, Any]]:
        destination = self._validate_destination(base_url, elevenlabs=True)
        headers = {"Accept": "application/json", "xi-api-key": api_key}
        voices_by_id: dict[str, dict[str, Any]] = {}
        token: str | None = None
        for _ in range(MAX_VOICE_PAGES):
            query = {
                "page_size": "100",
                "sort": "name",
                "sort_direction": "asc",
                "include_total_count": "false",
            }
            if token:
                query["next_page_token"] = token
            payload = self._json_request(
                destination,
                "GET",
                "v2/voices",
                headers=headers,
                query=query,
                elevenlabs=True,
            )
            if not isinstance(payload, dict) or not isinstance(
                payload.get("voices"), list
            ):
                raise ProviderError("ElevenLabs returned an invalid voice list.")
            for item in payload["voices"]:
                if not isinstance(item, dict):
                    continue
                voice_id, name = item.get("voice_id"), item.get("name")
                if not isinstance(voice_id, str) or not isinstance(name, str):
                    continue
                safe: dict[str, Any] = {"voice_id": voice_id, "name": name}
                for field in ("category", "description", "preview_url"):
                    if isinstance(item.get(field), str):
                        safe[field] = item[field]
                labels = item.get("labels")
                if isinstance(labels, dict):
                    safe["labels"] = {
                        str(key): value
                        for key, value in labels.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
                voices_by_id.setdefault(voice_id, safe)
            if payload.get("has_more") is not True:
                return sorted(
                    voices_by_id.values(),
                    key=lambda voice: (voice["name"], voice["voice_id"]),
                )
            token = payload.get("next_page_token")
            if not isinstance(token, str) or not token:
                raise ProviderError("ElevenLabs pagination was incomplete.")
        raise ProviderError("ElevenLabs returned too many voice pages.")

    def discover_tts_models(self, base_url: str, api_key: str) -> list[dict[str, str]]:
        destination = self._validate_destination(base_url, elevenlabs=True)
        payload = self._json_request(
            destination,
            "GET",
            "v1/models",
            headers={"Accept": "application/json", "xi-api-key": api_key},
            elevenlabs=True,
        )
        if not isinstance(payload, list):
            raise ProviderError("ElevenLabs returned an invalid TTS model list.")
        models = []
        for item in payload:
            if (
                not isinstance(item, dict)
                or item.get("can_do_text_to_speech") is not True
                or not isinstance(item.get("model_id"), str)
            ):
                continue
            safe = {"model_id": item["model_id"]}
            if isinstance(item.get("name"), str):
                safe["name"] = item["name"]
            models.append(safe)
        return sorted(models, key=lambda item: item["model_id"])

    def synthesize_speech(
        self,
        *,
        base_url: str,
        api_key: str | None,
        voice_id: str,
        text: str,
        model_id: str,
        output_format: str,
        provider: str = "elevenlabs",
    ) -> bytes:
        if provider not in {"elevenlabs", "voicebox"}:
            raise ProviderError("The voice provider is invalid.")
        if (
            self._transport is not None or self._speech_transport is not None
        ) and not isinstance(self._speech_transport, httpx.AsyncBaseTransport):
            raise ProviderError("Speech requires an async-compatible HTTP transport.")
        elevenlabs = provider == "elevenlabs"
        speech_timeout = (
            self._speech_total_timeout
            if elevenlabs
            else self._voicebox_speech_total_timeout
        )
        # Local CPU synthesis needs its own bounded budget, not the metadata limit.
        destination = self._validate_destination(
            base_url,
            elevenlabs=elevenlabs,
            voicebox=not elevenlabs,
            total_timeout=speech_timeout,
        )
        expected_format = "mp3_44100_128" if elevenlabs else "mp3"
        if not voice_id or len(voice_id) > 200 or output_format != expected_format:
            raise ProviderError("The voice settings are invalid.")
        with self._provider_slot(deadline=destination.deadline, elevenlabs=elevenlabs):
            # Keep the public API synchronous, but cancel blocked network I/O at
            # the deadline instead of abandoning a still-running worker thread.
            return asyncio.run(
                self._synthesize_speech(
                    destination,
                    provider=provider,
                    speech_timeout=speech_timeout,
                    api_key=api_key,
                    voice_id=voice_id,
                    text=text,
                    model_id=model_id,
                    output_format=output_format,
                )
            )

    async def _synthesize_speech(
        self,
        destination: _Destination,
        *,
        provider: str,
        speech_timeout: float,
        api_key: str | None,
        voice_id: str,
        text: str,
        model_id: str,
        output_format: str,
    ) -> bytes:
        elevenlabs = provider == "elevenlabs"
        prefix = "ElevenLabs" if elevenlabs else "Voicebox"
        headers = {"Accept": "audio/mpeg"}
        if elevenlabs:
            headers["xi-api-key"] = api_key or ""
            suffix = f"v1/text-to-speech/{quote(voice_id, safe='')}"
            query = {"output_format": output_format}
            body = {"text": text, "model_id": model_id}
        else:
            suffix = "v1/audio/speech"
            query = None
            body = {
                "input": text,
                "voice": voice_id,
                "model": model_id,
                "response_format": "mp3",
            }
        content = bytearray()
        try:
            # A per-call client is closed before asyncio.run closes its loop and
            # before the synchronous wrapper releases provider capacity.
            async with httpx.AsyncClient(
                transport=self._speech_transport, follow_redirects=False
            ) as client:
                headers, timeout, extensions = self._request_options(
                    destination,
                    headers,
                    elevenlabs=elevenlabs,
                    read_timeout=speech_timeout,
                )
                async with (
                    asyncio.timeout(
                        self._remaining(destination.deadline, elevenlabs=elevenlabs)
                    ),
                    client.stream(
                        "POST",
                        self._url(destination, suffix, query),
                        headers=headers,
                        json=body,
                        timeout=timeout,
                        extensions=extensions,
                    ) as response,
                ):
                    self._remaining(destination.deadline, elevenlabs=elevenlabs)
                    if response.is_redirect:
                        raise ProviderError(
                            f"{prefix} returned a redirect, which is not allowed."
                        )
                    if response.status_code >= 400:
                        raise ProviderError(
                            f"{prefix} returned HTTP {response.status_code}."
                        )
                    if (
                        not elevenlabs
                        and response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                        != "audio/mpeg"
                    ):
                        raise ProviderError("Voicebox returned invalid audio.")
                    async for chunk in response.aiter_bytes():
                        self._remaining(destination.deadline, elevenlabs=elevenlabs)
                        content.extend(chunk)
                        if len(content) > MAX_AUDIO_BYTES:
                            raise ProviderError(f"{prefix} returned invalid audio.")
                    self._remaining(destination.deadline, elevenlabs=elevenlabs)
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ProviderError(f"{prefix} timed out.") from error
        except httpx.HTTPError as error:
            raise ProviderError(f"{prefix} could not be reached.") from error
        if not content:
            raise ProviderError(f"{prefix} returned invalid audio.")
        if not elevenlabs and not (
            content.startswith(b"ID3")
            or (len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0)
        ):
            raise ProviderError("Voicebox returned invalid audio.")
        return bytes(content)
