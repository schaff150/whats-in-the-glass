import base64
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.models import (
    AnalysisCreate,
    AnalysisRecord,
    FeedbackCreate,
    FeedbackRecord,
    ModelSettingsView,
    SettingsView,
    VoiceSettingsView,
)


class SecretConfigurationError(RuntimeError):
    pass


class Database:
    def __init__(self, path: str | Path, secret_key: str | bytes | None = None):
        self.path = Path(path)
        self._secret_key = self._normalize_secret_key(secret_key)

    @staticmethod
    def _normalize_secret_key(secret_key: str | bytes | None) -> bytes | None:
        if not secret_key:
            return None
        value = secret_key.encode() if isinstance(secret_key, str) else secret_key
        if len(value) == 32:
            return value
        try:
            decoded = base64.b64decode(value, altchars=b"-_", validate=True)
        except (ValueError, TypeError):
            decoded = b""
        if len(decoded) != 32:
            raise ValueError("The provider master key must contain exactly 32 bytes.")
        return decoded

    def _encrypt_secret(self, value: str, field: str) -> bytes:
        if self._secret_key is None:
            raise SecretConfigurationError(
                "Secret storage is unavailable until WITG_SECRET_KEY_FILE is configured."
            )
        nonce = os.urandom(12)
        aad = f"connection:1:{field}:v1".encode()
        return (
            b"v1:"
            + nonce
            + AESGCM(self._secret_key).encrypt(nonce, value.encode(), aad)
        )

    def _decrypt_secret(self, encrypted: bytes, field: str) -> str:
        if self._secret_key is None:
            raise SecretConfigurationError(
                "Secret storage is unavailable until WITG_SECRET_KEY_FILE is configured."
            )
        if not encrypted.startswith(b"v1:") or len(encrypted) < 32:
            raise SecretConfigurationError("Stored credentials could not be decrypted.")
        nonce = encrypted[3:15]
        aad = f"connection:1:{field}:v1".encode()
        try:
            return AESGCM(self._secret_key).decrypt(nonce, encrypted[15:], aad).decode()
        except (InvalidTag, UnicodeDecodeError) as error:
            raise SecretConfigurationError(
                "Stored credentials cannot be decrypted with the provider master key."
            ) from error

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    drinker TEXT NOT NULL,
                    guest_name TEXT,
                    submitted_drink_name TEXT,
                    identified_title TEXT NOT NULL,
                    region TEXT NOT NULL,
                    age_statement TEXT NOT NULL,
                    abv TEXT NOT NULL,
                    style TEXT NOT NULL,
                    overview TEXT NOT NULL,
                    context TEXT NOT NULL,
                    tasting_notes TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    analysis_id INTEGER PRIMARY KEY,
                    rating INTEGER,
                    notes TEXT,
                    FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS connection_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    model_provider TEXT NOT NULL,
                    model_display_name TEXT NOT NULL,
                    model_base_url TEXT NOT NULL,
                    model_id TEXT,
                    model_api_key_encrypted BLOB,
                    voice_provider TEXT NOT NULL,
                    voice_display_name TEXT NOT NULL,
                    voice_base_url TEXT NOT NULL,
                    voice_id TEXT,
                    voice_name TEXT,
                    voice_api_key_encrypted BLOB,
                    tts_model_id TEXT NOT NULL,
                    output_format TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO connection_settings (
                    id, model_provider, model_display_name, model_base_url,
                    voice_provider, voice_display_name, voice_base_url,
                    tts_model_id, output_format
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "openai_compatible",
                    "OpenAI-compatible",
                    "https://api.openai.com/v1",
                    "elevenlabs",
                    "ElevenLabs",
                    "https://api.elevenlabs.io",
                    "eleven_multilingual_v2",
                    "mp3_44100_128",
                ),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS narrations (
                    token TEXT PRIMARY KEY,
                    analysis_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    UNIQUE (analysis_id, fingerprint),
                    FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
                )
                """
            )
            narration_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(narrations)")
            }
            if "last_used_at" not in narration_columns:
                connection.execute(
                    "ALTER TABLE narrations ADD COLUMN last_used_at TEXT"
                )
            connection.execute(
                """
                UPDATE narrations
                SET last_used_at = created_at
                WHERE last_used_at IS NULL
                """
            )

    def get_settings(self) -> SettingsView:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM connection_settings WHERE id = 1"
            ).fetchone()
        return SettingsView(
            model=ModelSettingsView(
                provider=row["model_provider"],
                display_name=row["model_display_name"],
                base_url=row["model_base_url"],
                model_id=row["model_id"],
                api_key_configured=row["model_api_key_encrypted"] is not None,
            ),
            voice=VoiceSettingsView(
                provider=row["voice_provider"],
                display_name=row["voice_display_name"],
                base_url=row["voice_base_url"],
                voice_id=row["voice_id"],
                voice_name=row["voice_name"],
                tts_model_id=row["tts_model_id"],
                output_format=row["output_format"],
                api_key_configured=row["voice_api_key_encrypted"] is not None,
            ),
        )

    def update_model_settings(
        self,
        *,
        display_name: str,
        base_url: str,
        model_id: str | None,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> SettingsView:
        if api_key and clear_api_key:
            raise ValueError("A provider key cannot be replaced and cleared together.")
        encrypted_key = None
        replace_key = bool(api_key)
        if replace_key:
            encrypted_key = self._encrypt_secret(api_key, "model_api_key")
        with self.connect() as connection:
            current = connection.execute(
                "SELECT model_provider, model_base_url FROM connection_settings WHERE id = 1"
            ).fetchone()
            endpoint_changed = (
                current["model_provider"] != "openai_compatible"
                or current["model_base_url"] != base_url
            )
            connection.execute(
                """
                UPDATE connection_settings SET
                    model_display_name = ?, model_base_url = ?, model_id = ?
                WHERE id = 1
                """,
                (display_name, base_url, model_id),
            )
            if replace_key or clear_api_key or endpoint_changed:
                connection.execute(
                    "UPDATE connection_settings SET model_api_key_encrypted = ? WHERE id = 1",
                    (encrypted_key,),
                )
        return self.get_settings()

    def get_model_api_key(self) -> str | None:
        with self.connect() as connection:
            encrypted = connection.execute(
                "SELECT model_api_key_encrypted FROM connection_settings WHERE id = 1"
            ).fetchone()[0]
        if encrypted is None:
            return None
        return self._decrypt_secret(encrypted, "model_api_key")

    def update_voice_settings(
        self,
        *,
        base_url: str,
        voice_id: str | None,
        voice_name: str | None,
        tts_model_id: str,
        output_format: str,
        api_key: str | None = None,
        clear_api_key: bool = False,
        provider: str = "elevenlabs",
    ) -> SettingsView:
        if provider not in {"elevenlabs", "voicebox"}:
            raise ValueError("The voice provider is invalid.")
        if provider == "voicebox" and api_key:
            raise ValueError("Voicebox does not use API keys.")
        if api_key and clear_api_key:
            raise ValueError("A provider key cannot be replaced and cleared together.")
        encrypted_key = (
            self._encrypt_secret(api_key, "voice_api_key") if api_key else None
        )
        with self.connect() as connection:
            current = connection.execute(
                "SELECT voice_provider, voice_base_url FROM connection_settings WHERE id = 1"
            ).fetchone()
            connection.execute(
                """
                UPDATE connection_settings SET voice_provider = ?, voice_display_name = ?,
                    voice_base_url = ?, voice_id = ?, voice_name = ?,
                    tts_model_id = ?, output_format = ?
                WHERE id = 1
                """,
                (
                    provider,
                    "Voicebox" if provider == "voicebox" else "ElevenLabs",
                    base_url,
                    voice_id,
                    voice_name,
                    tts_model_id,
                    output_format,
                ),
            )
            if (
                api_key
                or clear_api_key
                or provider == "voicebox"
                or current["voice_provider"] != provider
                or current["voice_base_url"] != base_url
            ):
                connection.execute(
                    "UPDATE connection_settings SET voice_api_key_encrypted = ? WHERE id = 1",
                    (encrypted_key,),
                )
        return self.get_settings()

    def get_voice_api_key(self) -> str | None:
        with self.connect() as connection:
            encrypted = connection.execute(
                "SELECT voice_api_key_encrypted FROM connection_settings WHERE id = 1"
            ).fetchone()[0]
        if encrypted is None:
            return None
        return self._decrypt_secret(encrypted, "voice_api_key")

    def get_narration(self, analysis_id: int, fingerprint: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM narrations WHERE analysis_id = ? AND fingerprint = ?",
                (analysis_id, fingerprint),
            ).fetchone()

    def get_narration_by_token(self, token: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM narrations WHERE token = ?", (token,)
            ).fetchone()

    def mark_narration_used(self, token: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE narrations SET last_used_at = ? WHERE token = ?",
                (datetime.now(UTC).isoformat(), token),
            )

    def save_narration(
        self,
        *,
        token: str,
        analysis_id: int,
        fingerprint: str,
        file_name: str,
    ) -> sqlite3.Row:
        used_at = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO narrations (
                    token, analysis_id, fingerprint, file_name, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    analysis_id,
                    fingerprint,
                    file_name,
                    used_at,
                    used_at,
                ),
            )
            connection.execute(
                """
                UPDATE narrations
                SET last_used_at = ?
                WHERE analysis_id = ? AND fingerprint = ?
                """,
                (used_at, analysis_id, fingerprint),
            )
        return self.get_narration(analysis_id, fingerprint)

    def save_analysis(self, analysis: AnalysisCreate) -> AnalysisRecord:
        created_at = datetime.now(UTC)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO analyses (
                    drinker, guest_name, submitted_drink_name, identified_title,
                    region, age_statement, abv, style, overview, context,
                    tasting_notes, sources_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis.drinker,
                    analysis.guest_name,
                    analysis.submitted_drink_name,
                    analysis.identified_title,
                    analysis.region,
                    analysis.age_statement,
                    analysis.abv,
                    analysis.style,
                    analysis.overview,
                    analysis.context,
                    analysis.tasting_notes,
                    json.dumps(analysis.sources),
                    created_at.isoformat(),
                ),
            )
            analysis_id = cursor.lastrowid
        return AnalysisRecord(
            id=analysis_id, created_at=created_at, **analysis.model_dump()
        )

    def save_feedback(
        self, analysis_id: int, feedback: FeedbackCreate
    ) -> FeedbackRecord:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback (analysis_id, rating, notes) VALUES (?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    rating = excluded.rating,
                    notes = excluded.notes
                """,
                (analysis_id, feedback.rating, feedback.notes),
            )
        return FeedbackRecord(analysis_id=analysis_id, **feedback.model_dump())

    def list_analyses(self, limit: int = 50, offset: int = 0) -> list[AnalysisRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT analyses.*, feedback.rating, feedback.notes,
                    (
                        SELECT narrations.token
                        FROM narrations
                        WHERE narrations.analysis_id = analyses.id
                        ORDER BY COALESCE(
                            narrations.last_used_at, narrations.created_at
                        ) DESC, narrations.created_at DESC, narrations.token DESC
                        LIMIT 1
                    ) AS narration_token
                FROM analyses
                LEFT JOIN feedback ON feedback.analysis_id = analyses.id
                ORDER BY analyses.created_at DESC, analyses.id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [self._analysis_from_row(row) for row in rows]

    def get_analysis(self, analysis_id: int) -> AnalysisRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        return self._analysis_from_row(row) if row else None

    @staticmethod
    def _analysis_from_row(row: sqlite3.Row) -> AnalysisRecord:
        values = dict(row)
        narration_token = values.pop("narration_token", None)
        values["narration_url"] = (
            f"/api/audio/{narration_token}" if narration_token else None
        )
        values["sources"] = json.loads(values.pop("sources_json"))
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        return AnalysisRecord.model_validate(values)
