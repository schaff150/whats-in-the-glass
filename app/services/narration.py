import hashlib
import json
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

from app.db import Database, SecretConfigurationError
from app.providers import ProviderClient, ProviderError


class NarrationConfigurationError(RuntimeError):
    pass


class NarrationService:
    def __init__(self, database: Database, providers: ProviderClient, data_dir: Path):
        self._database = database
        self._providers = providers
        self._audio_dir = data_dir / "narration"
        self._flights: dict[str, tuple[threading.Lock, int]] = {}
        self._flights_guard = threading.Lock()

    def generate(self, analysis_id: int) -> str:
        analysis = self._database.get_analysis(analysis_id)
        if analysis is None:
            raise LookupError("Analysis not found.")
        settings = self._database.get_settings().voice
        if not settings.voice_id or not settings.tts_model_id:
            raise NarrationConfigurationError(
                "ElevenLabs voice settings are incomplete."
            )
        try:
            api_key = self._database.get_voice_api_key()
        except SecretConfigurationError as error:
            raise NarrationConfigurationError(
                "ElevenLabs credentials are unavailable."
            ) from error
        if not api_key:
            raise NarrationConfigurationError("ElevenLabs credentials are incomplete.")
        prose = self._narration_text(analysis)
        fingerprint_input = {
            "analysis_id": analysis.id,
            "text": prose,
            "base_url": settings.base_url,
            "voice_id": settings.voice_id,
            "tts_model_id": settings.tts_model_id,
            "output_format": settings.output_format,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_input, sort_keys=True).encode()
        ).hexdigest()
        with self._single_flight(fingerprint):
            return self._generate_uncached(
                analysis_id=analysis_id,
                fingerprint=fingerprint,
                settings=settings,
                api_key=api_key,
                prose=prose,
            )

    @contextmanager
    def _single_flight(self, fingerprint: str):
        with self._flights_guard:
            lock, users = self._flights.get(fingerprint, (threading.Lock(), 0))
            self._flights[fingerprint] = (lock, users + 1)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._flights_guard:
                current_lock, current_users = self._flights[fingerprint]
                if current_users == 1:
                    self._flights.pop(fingerprint, None)
                else:
                    self._flights[fingerprint] = (current_lock, current_users - 1)

    def _generate_uncached(
        self, *, analysis_id: int, fingerprint: str, settings, api_key: str, prose: str
    ) -> str:
        cached = self._database.get_narration(analysis_id, fingerprint)
        if cached is not None and self._safe_path(cached["file_name"]).is_file():
            self._database.mark_narration_used(cached["token"])
            return cached["token"]
        audio = self._providers.synthesize_speech(
            base_url=settings.base_url,
            api_key=api_key,
            voice_id=settings.voice_id,
            text=prose,
            model_id=settings.tts_model_id,
            output_format=settings.output_format,
        )
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{fingerprint}.mp3"
        destination = self._safe_path(file_name)
        temporary = destination.with_name(f".{fingerprint}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_bytes(audio)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        row = self._database.save_narration(
            token=fingerprint,
            analysis_id=analysis_id,
            fingerprint=fingerprint,
            file_name=file_name,
        )
        return row["token"]

    def audio_path(self, token: str) -> Path | None:
        if len(token) != 64 or any(
            character not in "0123456789abcdef" for character in token
        ):
            return None
        row = self._database.get_narration_by_token(token)
        if row is None or row["file_name"] != f"{token}.mp3":
            return None
        path = self._safe_path(row["file_name"])
        return path if path.is_file() else None

    def _safe_path(self, file_name: str) -> Path:
        path = (self._audio_dir / file_name).resolve()
        if path.parent != self._audio_dir.resolve():
            raise ProviderError("The cached audio reference is invalid.")
        return path

    @staticmethod
    def _narration_text(analysis) -> str:
        return " ".join(
            [
                analysis.identified_title + ".",
                analysis.overview,
                analysis.context,
                "In the glass: " + analysis.tasting_notes,
            ]
        )
