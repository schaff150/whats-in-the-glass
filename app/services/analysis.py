import base64
import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db import Database, SecretConfigurationError
from app.providers import ProviderClient, ProviderError


@dataclass(frozen=True)
class AnalysisContent:
    identified_title: str
    region: str
    age_statement: str
    abv: str
    style: str
    overview: str
    context: str
    tasting_notes: str
    sources: list[str]


@dataclass(frozen=True)
class PhotoInput:
    filename: str | None
    content_type: str
    content: bytes


class AnalysisService(Protocol):
    def analyze(
        self, drink_name: str | None, photo: PhotoInput | None
    ) -> AnalysisContent: ...


class DemoAnalysisService:
    """Deterministic placeholder; it performs no AI or web research."""

    def analyze(
        self, drink_name: str | None, photo: PhotoInput | None
    ) -> AnalysisContent:
        if photo is not None and not drink_name:
            return AnalysisContent(
                identified_title="Bottle photograph — identification pending",
                region="Not identified",
                age_statement="Not identified",
                abv="Not identified",
                style="Photo identification pending",
                overview=(
                    "The photograph was accepted, but this demo has no image "
                    "recognition and has not identified the bottle."
                ),
                context=(
                    "A future multimodal analysis service will inspect the label and "
                    "ground its findings in researched sources."
                ),
                tasting_notes=(
                    "No tasting notes are offered until the bottle is genuinely "
                    "identified."
                ),
                sources=[],
            )
        if drink_name and "glenrothes" not in drink_name.casefold():
            return AnalysisContent(
                identified_title=drink_name,
                region="Not verified in demo",
                age_statement="Not verified",
                abv="Not verified",
                style="Demonstration only",
                overview=(
                    f"{drink_name} is shown in a demonstration briefing while the "
                    "real model and research service is not connected."
                ),
                context=(
                    "The current demo service does not identify or research unknown "
                    "bottles. No facts have been inferred for this submission."
                ),
                tasting_notes=(
                    "Tasting guidance is intentionally unavailable until a grounded "
                    "analysis service is integrated."
                ),
                sources=[],
            )
        return AnalysisContent(
            identified_title="Gordon & MacPhail Discovery Glenrothes 11",
            region="Speyside",
            age_statement="11 years",
            abv="43%",
            style="Sherry-matured single malt",
            overview=(
                "The bottler matters nearly as much as the distillery here. "
                "Gordon & MacPhail has selected and matured Scottish spirit since 1895."
            ),
            context=(
                "This is an approved demonstration briefing, not live AI or web "
                "research. The Discovery range groups bottles by flavour."
            ),
            tasting_notes=(
                "Give it ten minutes neat and look for toasted malt, cinnamon, "
                "toffee, orchard fruit and orange peel."
            ),
            sources=[
                "Gordon & MacPhail — official product information",
                "The Glenrothes — distillery history",
            ],
        )


class _AnalysisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identified_title: str = Field(min_length=1, max_length=500)
    region: str = Field(min_length=1, max_length=500)
    age_statement: str = Field(min_length=1, max_length=500)
    abv: str = Field(min_length=1, max_length=500)
    style: str = Field(min_length=1, max_length=500)
    overview: str = Field(min_length=1, max_length=5000)
    context: str = Field(min_length=1, max_length=5000)
    tasting_notes: str = Field(min_length=1, max_length=5000)
    sources: list[str] = Field(default_factory=list, max_length=50)


class ConfiguredAnalysisService:
    def __init__(
        self,
        database: Database,
        providers: ProviderClient,
        fallback: AnalysisService | None = None,
    ):
        self._database = database
        self._providers = providers
        self._fallback = fallback or DemoAnalysisService()

    def analyze(
        self, drink_name: str | None, photo: PhotoInput | None
    ) -> AnalysisContent:
        settings = self._database.get_settings().model
        if not settings.model_id:
            return self._fallback.analyze(drink_name, photo)
        try:
            api_key = self._database.get_model_api_key()
        except SecretConfigurationError as error:
            raise ProviderError("The model credentials are unavailable.") from error
        user_content: str | list[dict[str, object]]
        prompt = (
            "Analyze this bottle and return only a JSON object with identified_title, "
            "region, age_statement, abv, style, overview, context, tasting_notes, and "
            "sources. Do not claim grounded research. Bottle name: "
            f"{drink_name or 'not supplied'}"
        )
        if photo is None:
            user_content = prompt
        else:
            encoded = base64.b64encode(photo.content).decode()
            user_content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{photo.content_type};base64,{encoded}",
                    },
                },
            ]
        raw = self._providers.chat_completion(
            base_url=settings.base_url,
            api_key=api_key,
            model_id=settings.model_id,
            messages=[
                {
                    "role": "system",
                    "content": "Return valid JSON matching the requested schema.",
                },
                {"role": "user", "content": user_content},
            ],
        )
        try:
            payload = _AnalysisPayload.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as error:
            raise ProviderError("The model returned an invalid analysis.") from error
        values = payload.model_dump(exclude={"sources"})
        return AnalysisContent(**values, sources=[])
