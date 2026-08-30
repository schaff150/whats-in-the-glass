import os
import socket
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api import MAX_REQUEST_BYTES, create_api_router
from app.db import Database, SecretConfigurationError
from app.limits import PerClientRateLimiter, RateLimitExceeded
from app.middleware import RequestBodyLimitMiddleware
from app.models import (
    ELEVENLABS_HOSTS,
    ModelSettingsUpdate,
    ProviderProbeRequest,
    VoicePreviewRequest,
    VoiceSettingsUpdate,
    validate_base_url,
)
from app.providers import (
    ProviderCapacityError,
    ProviderClient,
    ProviderError,
    ProviderRateLimitError,
)
from app.security import validate_exact_origin
from app.services.analysis import (
    AnalysisService,
    ConfiguredAnalysisService,
    DemoAnalysisService,
)
from app.services.narration import NarrationConfigurationError, NarrationService

STATIC_DIR = Path(__file__).parent / "static"


def _read_setting_file(name: str, *, binary: bool) -> str | bytes | None:
    path = os.environ.get(name)
    if not path:
        return None
    try:
        content = Path(path).read_bytes()
    except OSError:
        return None
    if binary:
        return content if len(content) == 32 else content.strip()
    try:
        return content.decode().strip()
    except UnicodeDecodeError:
        return None


def _env_number(name: str, default, cast, minimum, maximum):
    try:
        value = cast(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if minimum <= value <= maximum else default


def create_app(
    data_dir: str | Path | None = None,
    analysis_service: AnalysisService | None = None,
    secret_key: str | bytes | None = None,
    allow_insecure_credentials: bool = True,
    provider_transport: httpx.BaseTransport | None = None,
    provider_resolver=socket.getaddrinfo,
    model_host_allowlist: set[str] | None = None,
    analysis_rate_limit: int | None = None,
    narration_rate_limit: int | None = None,
    rate_limit_window: float | None = None,
    trusted_proxy_ips: set[str] | None = None,
    provider_max_concurrency: int | None = None,
    provider_request_budget: int | None = None,
    provider_total_timeout: float | None = None,
) -> FastAPI:
    resolved_data_dir = Path(data_dir or os.environ.get("WITG_DATA_DIR", "data"))
    if secret_key is None:
        secret_key = _read_setting_file("WITG_SECRET_KEY_FILE", binary=True)
    database = Database(resolved_data_dir / "witg.sqlite3", secret_key=secret_key)
    database.initialize()
    if model_host_allowlist is None:
        model_host_allowlist = {
            host.strip()
            for host in os.environ.get("WITG_MODEL_HOST_ALLOWLIST", "").split(",")
            if host.strip()
        }
    providers = ProviderClient(
        transport=provider_transport,
        resolver=provider_resolver,
        model_host_allowlist=model_host_allowlist,
        max_concurrency=provider_max_concurrency
        or _env_number("WITG_PROVIDER_MAX_CONCURRENCY", 4, int, 1, 64),
        request_budget=provider_request_budget
        or _env_number("WITG_PROVIDER_REQUEST_BUDGET", 120, int, 1, 100_000),
        request_budget_window=_env_number(
            "WITG_PROVIDER_BUDGET_WINDOW_SECONDS", 60.0, float, 1.0, 86_400.0
        ),
        total_timeout=provider_total_timeout
        or _env_number("WITG_PROVIDER_TOTAL_TIMEOUT_SECONDS", 20.0, float, 1.0, 300.0),
    )
    analysis_limiter = PerClientRateLimiter(
        analysis_rate_limit
        or _env_number("WITG_ANALYSIS_RATE_LIMIT", 10, int, 1, 100_000),
        rate_limit_window
        or _env_number("WITG_RATE_LIMIT_WINDOW_SECONDS", 60.0, float, 1.0, 86_400.0),
    )
    narration_limiter = PerClientRateLimiter(
        narration_rate_limit
        or _env_number("WITG_NARRATION_RATE_LIMIT", 6, int, 1, 100_000),
        rate_limit_window
        or _env_number("WITG_RATE_LIMIT_WINDOW_SECONDS", 60.0, float, 1.0, 86_400.0),
    )

    if trusted_proxy_ips is None:
        trusted_proxy_ips = {
            value.strip()
            for value in os.environ.get("WITG_TRUSTED_PROXY_IPS", "").split(",")
            if value.strip()
        }

    app = FastAPI(title="What’s in the Glass?")
    app.add_middleware(
        ProxyHeadersMiddleware,
        trusted_hosts=list(trusted_proxy_ips),
    )
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)
    app.state.data_dir = resolved_data_dir
    app.state.database = database
    app.state.provider_client = providers
    app.state.analysis_service = analysis_service or ConfiguredAnalysisService(
        database, providers, DemoAnalysisService()
    )
    narration_service = NarrationService(database, providers, resolved_data_dir)
    app.state.narration_service = narration_service
    app.include_router(
        create_api_router(database, app.state.analysis_service, analysis_limiter)
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(
        request: Request, error: RequestValidationError
    ):
        if request.url.path.startswith(("/api/admin/", "/api/settings")):
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={"detail": "Invalid request."},
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": jsonable_encoder(error.errors())},
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "service": "whats-in-the-glass"}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(("/api/admin/", "/api/settings")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_same_origin_mutation(request: Request):
        validate_exact_origin(request)

    def probe_api_key(
        probe: ProviderProbeRequest,
        saved_url: str,
        getter,
        *,
        elevenlabs: bool = False,
    ) -> tuple[str, str | None]:
        official_hosts = ELEVENLABS_HOSTS if elevenlabs else None
        try:
            target_url = validate_base_url(
                probe.base_url or saved_url, official_hosts=official_hosts
            )
            canonical_saved_url = validate_base_url(
                saved_url, official_hosts=official_hosts
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid provider endpoint.",
            ) from error
        if probe.api_key:
            return target_url, probe.api_key
        if target_url != canonical_saved_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="An explicit key is required for an unsaved provider endpoint.",
            )
        return target_url, getter()

    @app.get("/api/settings")
    def settings(request: Request):
        return database.get_settings()

    @app.put("/api/settings/model")
    def save_model_settings(update: ModelSettingsUpdate, request: Request):
        require_same_origin_mutation(request)
        if update.base_url.startswith("http://"):
            try:
                providers.validate_model_endpoint(update.base_url)
            except ProviderError as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="The HTTP model endpoint is not operator-allowlisted.",
                ) from error
        try:
            settings = database.update_model_settings(
                display_name=update.display_name,
                base_url=update.base_url,
                model_id=update.model_id,
                api_key=update.api_key,
                clear_api_key=update.clear_api_key,
            )
        except SecretConfigurationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secret storage is not configured.",
            ) from error
        return settings.model

    @app.put("/api/settings/voice")
    def save_voice_settings(update: VoiceSettingsUpdate, request: Request):
        require_same_origin_mutation(request)
        try:
            settings = database.update_voice_settings(
                base_url=update.base_url,
                voice_id=update.voice_id,
                voice_name=update.voice_name,
                tts_model_id=update.tts_model_id,
                output_format=update.output_format,
                api_key=update.api_key,
                clear_api_key=update.clear_api_key,
            )
        except SecretConfigurationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secret storage is not configured.",
            ) from error
        return settings.voice

    @app.post("/api/settings/model/models")
    def discover_models(probe: ProviderProbeRequest, request: Request):
        require_same_origin_mutation(request)
        settings = database.get_settings().model
        try:
            target_url, api_key = probe_api_key(
                probe, settings.base_url, database.get_model_api_key
            )
            models = providers.discover_models(target_url, api_key)
        except SecretConfigurationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secret storage is not configured.",
            ) from error
        except ProviderError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Model discovery failed.",
            ) from error
        return {"models": models}

    @app.post("/api/settings/model/test")
    def test_model_connection(probe: ProviderProbeRequest, request: Request):
        result = discover_models(probe, request)
        return {"ok": True, "models_count": len(result["models"])}

    def _discover_voices(probe: ProviderProbeRequest):
        settings = database.get_settings().voice
        try:
            target_url, api_key = probe_api_key(
                probe,
                settings.base_url,
                database.get_voice_api_key,
                elevenlabs=True,
            )
            if not api_key:
                raise SecretConfigurationError("Voice key is not configured.")
            return providers.discover_voices(target_url, api_key)
        except SecretConfigurationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ElevenLabs credentials are not configured.",
            ) from error
        except ProviderError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Voice discovery failed.",
            ) from error

    @app.post("/api/settings/voice/voices")
    def discover_voices(probe: ProviderProbeRequest, request: Request):
        require_same_origin_mutation(request)
        return {"voices": _discover_voices(probe)}

    @app.post("/api/settings/voice/models")
    def discover_voice_models(probe: ProviderProbeRequest, request: Request):
        require_same_origin_mutation(request)
        settings = database.get_settings().voice
        try:
            target_url, api_key = probe_api_key(
                probe,
                settings.base_url,
                database.get_voice_api_key,
                elevenlabs=True,
            )
            if not api_key:
                raise SecretConfigurationError("Voice key is not configured.")
            models = providers.discover_tts_models(target_url, api_key)
        except SecretConfigurationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ElevenLabs credentials are not configured.",
            ) from error
        except ProviderError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Voice model discovery failed.",
            ) from error
        return {"models": models}

    @app.post("/api/settings/voice/test")
    def test_voice_connection(probe: ProviderProbeRequest, request: Request):
        require_same_origin_mutation(request)
        voices = _discover_voices(probe)
        return {"ok": True, "voices_count": len(voices)}

    @app.post("/api/settings/voice/preview")
    def preview_voice(preview: VoicePreviewRequest, request: Request):
        require_same_origin_mutation(request)
        settings = database.get_settings().voice
        voice_id = preview.voice_id or settings.voice_id
        if not voice_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Select an ElevenLabs voice before previewing.",
            )
        probe = ProviderProbeRequest(
            base_url=preview.base_url,
            api_key=preview.api_key,
        )
        try:
            target_url, api_key = probe_api_key(
                probe,
                settings.base_url,
                database.get_voice_api_key,
                elevenlabs=True,
            )
            if not api_key:
                raise SecretConfigurationError("Voice key is not configured.")
            audio = providers.synthesize_speech(
                base_url=target_url,
                api_key=api_key,
                voice_id=voice_id,
                text=preview.text,
                model_id=preview.tts_model_id or settings.tts_model_id,
                output_format=preview.output_format or settings.output_format,
            )
        except SecretConfigurationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ElevenLabs credentials are not configured.",
            ) from error
        except ProviderError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Voice preview failed.",
            ) from error
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/analyses/{analysis_id}/narration")
    def generate_narration(analysis_id: int, request: Request) -> dict[str, str]:
        client_id = request.client.host if request.client else "unknown"
        try:
            narration_limiter.check(client_id)
        except RateLimitExceeded as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Narration rate limit exceeded. Try again later.",
            ) from error
        try:
            token = narration_service.generate(analysis_id)
        except LookupError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found."
            ) from error
        except NarrationConfigurationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error
        except (ProviderCapacityError, ProviderRateLimitError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Narration is temporarily busy.",
            ) from error
        except ProviderError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Narration could not be generated.",
            ) from error
        return {"audio_url": f"/api/audio/{token}"}

    @app.get("/api/audio/{token}")
    def narration_audio(token: str):
        path = narration_service.audio_path(token)
        if path is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Audio not found."
            )
        return FileResponse(
            path,
            media_type="audio/mpeg",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/service-worker.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "service-worker.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    return app


app = create_app()
