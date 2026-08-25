import base64
import json
import sqlite3

import pytest

from app.db import Database, SecretConfigurationError

SECRET_KEY = base64.urlsafe_b64encode(b"test-aes-gcm-key-material-32byte")


def test_database_initializes_default_write_only_connection_settings(tmp_path):
    database = Database(tmp_path / "witg.sqlite3")
    database.initialize()

    settings = database.get_settings()

    assert settings.model.provider == "openai_compatible"
    assert settings.model.display_name == "OpenAI-compatible"
    assert settings.model.base_url == "https://api.openai.com/v1"
    assert settings.model.model_id is None
    assert settings.model.api_key_configured is False
    assert settings.voice.provider == "elevenlabs"
    assert settings.voice.base_url == "https://api.elevenlabs.io"
    assert settings.voice.voice_id is None
    assert settings.voice.tts_model_id == "eleven_multilingual_v2"
    assert settings.voice.output_format == "mp3_44100_128"
    assert settings.voice.api_key_configured is False
    assert "api_key" not in settings.model.model_dump()
    assert "api_key" not in settings.voice.model_dump()


def test_missing_master_key_keeps_database_healthy_but_secret_save_fails(tmp_path):
    database = Database(tmp_path / "witg.sqlite3")
    database.initialize()

    with pytest.raises(SecretConfigurationError, match="WITG_SECRET_KEY_FILE"):
        database.update_model_settings(
            display_name="Model",
            base_url="https://models.example/v1",
            model_id="model-1",
            api_key="missing-key-operation-canary",
        )

    assert database.get_settings().model.api_key_configured is False


def test_model_api_key_is_encrypted_at_rest_and_write_only(tmp_path):
    path = tmp_path / "witg.sqlite3"
    database = Database(path, secret_key=SECRET_KEY)
    database.initialize()

    database.update_model_settings(
        display_name="Local model",
        base_url="http://modelbox.local/v1",
        model_id="vision-model",
        api_key="top-secret-model-key",
    )

    assert database.get_model_api_key() == "top-secret-model-key"
    assert database.get_settings().model.api_key_configured is True
    assert b"top-secret-model-key" not in path.read_bytes()
    assert "top-secret-model-key" not in database.get_settings().model_dump_json()


def test_blank_model_key_preserves_and_explicit_clear_removes_secret(tmp_path):
    database = Database(tmp_path / "witg.sqlite3", secret_key=SECRET_KEY)
    database.initialize()
    values = {
        "display_name": "Model",
        "base_url": "https://models.example/v1",
        "model_id": "model-1",
    }
    database.update_model_settings(**values, api_key="existing-secret")

    database.update_model_settings(**values, api_key="")
    assert database.get_model_api_key() == "existing-secret"

    database.update_model_settings(**values, clear_api_key=True)
    assert database.get_model_api_key() is None
    assert database.get_settings().model.api_key_configured is False


def test_voice_settings_encrypt_preserve_and_clear_key(tmp_path):
    path = tmp_path / "witg.sqlite3"
    database = Database(path, secret_key=SECRET_KEY)
    database.initialize()
    values = {
        "base_url": "https://voice.example",
        "voice_id": "voice-safe-id",
        "voice_name": "Narrator",
        "tts_model_id": "eleven_turbo_v2_5",
        "output_format": "mp3_44100_128",
    }
    database.update_voice_settings(**values, api_key="voice-secret")

    database.update_voice_settings(**values, api_key="")
    assert database.get_voice_api_key() == "voice-secret"
    assert b"voice-secret" not in path.read_bytes()

    database.update_voice_settings(**values, clear_api_key=True)
    assert database.get_voice_api_key() is None
    assert database.get_settings().voice.api_key_configured is False


def test_model_endpoint_change_clears_old_key_without_atomic_replacement(tmp_path):
    database = Database(tmp_path / "witg.sqlite3", secret_key=SECRET_KEY)
    database.initialize()
    database.update_model_settings(
        display_name="First",
        base_url="https://first.example/v1",
        model_id="model-1",
        api_key="first-origin-key-canary",
    )

    database.update_model_settings(
        display_name="Second",
        base_url="https://second.example/v1",
        model_id="model-2",
        api_key="",
    )

    assert database.get_model_api_key() is None
    assert database.get_settings().model.api_key_configured is False


def test_database_defensively_rejects_replace_and_clear_secret_conflict(tmp_path):
    database = Database(tmp_path / "witg.sqlite3", secret_key=SECRET_KEY)
    database.initialize()
    model = {
        "display_name": "Model",
        "base_url": "https://models.example/v1",
        "model_id": "model",
    }
    voice = {
        "base_url": "https://api.elevenlabs.io",
        "voice_id": "voice",
        "voice_name": "Voice",
        "tts_model_id": "eleven_multilingual_v2",
        "output_format": "mp3_44100_128",
    }

    with pytest.raises(ValueError, match="replaced and cleared"):
        database.update_model_settings(
            **model, api_key="replacement", clear_api_key=True
        )
    with pytest.raises(ValueError, match="replaced and cleared"):
        database.update_voice_settings(
            **voice, api_key="replacement", clear_api_key=True
        )

    assert database.get_model_api_key() is None
    assert database.get_voice_api_key() is None


def test_initialize_migrates_legacy_analysis_and_feedback_without_data_loss(tmp_path):
    path = tmp_path / "witg.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE analyses (
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
            );
            CREATE TABLE feedback (
                analysis_id INTEGER PRIMARY KEY,
                rating INTEGER,
                notes TEXT,
                FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            """
            INSERT INTO analyses VALUES (
                7, 'Other', 'Legacy guest', 'Legacy bottle', 'Legacy title',
                'Legacy region', '12 years', '46%', 'Legacy style',
                'Legacy overview', 'Legacy context', 'Legacy notes', ?,
                '2025-01-02T03:04:05+00:00'
            )
            """,
            (json.dumps(["Legacy source"]),),
        )
        connection.execute("INSERT INTO feedback VALUES (7, 5, 'Keep this note')")

    database = Database(path, secret_key=SECRET_KEY)
    database.initialize()

    record = database.get_analysis(7)
    history = database.list_analyses()
    assert record is not None
    assert record.identified_title == "Legacy title"
    assert record.sources == ["Legacy source"]
    assert history[0].id == 7
    assert history[0].rating == 5
    assert history[0].notes == "Keep this note"
    assert database.get_settings().model.base_url == "https://api.openai.com/v1"
