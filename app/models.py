from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

Drinker = Literal["Me", "Wife", "Other"]


class AnalysisCreate(BaseModel):
    drinker: Drinker
    guest_name: str | None = None
    submitted_drink_name: str | None = None
    identified_title: str
    region: str
    age_statement: str
    abv: str
    style: str
    overview: str
    context: str
    tasting_notes: str
    sources: list[str]


class FeedbackCreate(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=1000)


class FeedbackRecord(FeedbackCreate):
    analysis_id: int


class AnalysisRecord(AnalysisCreate):
    id: int
    created_at: datetime
    rating: int | None = None
    notes: str | None = None


class ModelSettingsView(BaseModel):
    provider: Literal["openai_compatible"] = "openai_compatible"
    display_name: str = "OpenAI-compatible"
    base_url: str = "https://api.openai.com/v1"
    model_id: str | None = None
    api_key_configured: bool = False


class VoiceSettingsView(BaseModel):
    provider: Literal["elevenlabs"] = "elevenlabs"
    display_name: str = "ElevenLabs"
    base_url: str = "https://api.elevenlabs.io"
    voice_id: str | None = None
    voice_name: str | None = None
    tts_model_id: str = "eleven_multilingual_v2"
    output_format: str = "mp3_44100_128"
    api_key_configured: bool = False


class SettingsView(BaseModel):
    model: ModelSettingsView
    voice: VoiceSettingsView


def validate_base_url(value: str, *, official_hosts: set[str] | None = None) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or (official_hosts is not None and parsed.scheme != "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Base URL must be an HTTPS origin/path without credentials, query, or fragment."
        )
    if official_hosts is not None and parsed.hostname.casefold() not in official_hosts:
        raise ValueError("Base URL must use an official ElevenLabs API origin.")
    if official_hosts is not None and parsed.port not in {None, 443}:
        raise ValueError("ElevenLabs base URL must use the standard HTTPS port.")
    if official_hosts is not None and parsed.path not in {"", "/"}:
        raise ValueError("ElevenLabs base URL must be an official API origin.")
    return value


class ModelSettingsUpdate(BaseModel):
    provider: Literal["openai_compatible"] = "openai_compatible"
    display_name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(max_length=500)
    model_id: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        return validate_base_url(value)

    @field_validator("api_key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        return value.strip() if value else None

    @model_validator(mode="after")
    def key_action_is_unambiguous(self):
        if self.api_key and self.clear_api_key:
            raise ValueError("A key cannot be replaced and cleared together.")
        return self


ELEVENLABS_HOSTS = {
    "api.elevenlabs.io",
    "api.us.elevenlabs.io",
    "api.eu.residency.elevenlabs.io",
    "api.in.residency.elevenlabs.io",
    "api.sg.residency.elevenlabs.io",
}


class VoiceSettingsUpdate(BaseModel):
    provider: Literal["elevenlabs"] = "elevenlabs"
    base_url: str = Field(max_length=500)
    voice_id: str | None = Field(default=None, max_length=200)
    voice_name: str | None = Field(default=None, max_length=200)
    tts_model_id: str = Field(min_length=1, max_length=200)
    output_format: Literal["mp3_44100_128"] = "mp3_44100_128"
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        return validate_base_url(value, official_hosts=ELEVENLABS_HOSTS)

    @field_validator("api_key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        return value.strip() if value else None

    @model_validator(mode="after")
    def key_action_is_unambiguous(self):
        if self.api_key and self.clear_api_key:
            raise ValueError("A key cannot be replaced and cleared together.")
        return self


class ProviderProbeRequest(BaseModel):
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096, repr=False)

    @field_validator("api_key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class VoicePreviewRequest(BaseModel):
    text: str = Field(
        default="This is a short What’s in the Glass voice preview.",
        min_length=1,
        max_length=300,
    )
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    voice_id: str | None = Field(default=None, max_length=200)
    tts_model_id: str | None = Field(default=None, max_length=200)
    output_format: Literal["mp3_44100_128"] | None = None

    @field_validator("api_key")
    @classmethod
    def normalize_key(cls, value: str | None) -> str | None:
        return value.strip() if value else None
