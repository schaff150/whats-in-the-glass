import base64
import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
        self, drinker: str, drink_name: str | None, photo: PhotoInput | None
    ) -> AnalysisContent: ...


class DemoAnalysisService:
    """Deterministic placeholder; it performs no AI or web research."""

    def analyze(
        self, drinker: str, drink_name: str | None, photo: PhotoInput | None
    ) -> AnalysisContent:
        del drinker  # The demo briefing does not adopt the Hamish persona.
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
    sources: object = None

    @model_validator(mode="after")
    def briefing_is_substantive(self):
        section_words = {
            "overview": len(self.overview.split()),
            "context": len(self.context.split()),
            "tasting_notes": len(self.tasting_notes.split()),
        }
        if (
            section_words["overview"] < 45
            or section_words["context"] < 65
            or section_words["tasting_notes"] < 45
            or sum(section_words.values()) < 180
        ):
            raise ValueError("The analysis briefing is too brief.")
        return self


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
        self, drinker: str, drink_name: str | None, photo: PhotoInput | None
    ) -> AnalysisContent:
        settings = self._database.get_settings().model
        if not settings.model_id:
            return self._fallback.analyze(drinker, drink_name, photo)
        try:
            api_key = self._database.get_model_api_key()
        except SecretConfigurationError as error:
            raise ProviderError("The model credentials are unavailable.") from error
        user_content: str | list[dict[str, object]]
        prompt = (
            "You are Hamish, a cantankerous but deeply knowledgeable drinks guide. "
            "Write the briefing in his voice: warm and enthusiastic when the drink "
            "earns it, dry and opinionated when it does not, never sycophantic. "
            "Include at most one short cantankerous remark — a good-natured dig at "
            "the drink, or when the drinker is 'Me', a pointed jab at them if this "
            "looks like a step back in quality from what they usually pour. Keep the "
            "remark to a single sentence and never let it crowd out the briefing. "
            "Identify and explain this drink for a curious person deciding what is "
            "in their glass. Return only one JSON object with identified_title, "
            "region, "
            "age_statement, abv, style, overview, context, tasting_notes, and sources. "
            "Write a substantive specialist briefing, not a label transcription or "
            "terse catalogue entry. Overview: 60–95 words covering what it is, who "
            "made or bottled it, and the most useful defining facts. Context: 85–145 "
            "words explaining the producer, origin, production or maturation, history, "
            "and why this expression is interesting; adapt those topics sensibly for "
            "whisky, gin, wine, beer, or another drink. Tasting notes: 60–105 words "
            "covering aroma, palate, finish, likely texture, and a practical serving "
            "suggestion. These word ranges are flexible guides, not hard limits; "
            "keep each section a little more succinct without losing its useful detail. "
            "Distinguish facts visible in the supplied name or label from "
            "general knowledge and cautious expectations. State uncertainty plainly. "
            "Do not invent sources, awards, prices, production details, or a claim that "
            "you browsed the web. Set sources to an empty array because this request has "
            "no grounded search tool. "
            f"Bottle name: {drink_name or 'not supplied'}. "
            f"Drinker: {drinker}."
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
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Hamish, a cantankerous but knowledgeable drinks guide. "
                    "Return valid JSON matching the requested schema and completeness "
                    "requirements, with the persona guidance from the user message."
                ),
            },
            {"role": "user", "content": user_content},
        ]
        raw = self._providers.chat_completion(
            base_url=settings.base_url,
            api_key=api_key,
            model_id=settings.model_id,
            messages=messages,
        )
        try:
            payload = _AnalysisPayload.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            repaired = self._providers.chat_completion(
                base_url=settings.base_url,
                api_key=api_key,
                model_id=settings.model_id,
                messages=[
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "The previous draft was invalid or too brief. Produce a "
                            "fresh replacement that satisfies every requested field and "
                            "word range. Return only the complete JSON object."
                        ),
                    },
                ],
            )
            try:
                payload = _AnalysisPayload.model_validate(json.loads(repaired))
            except (json.JSONDecodeError, ValidationError) as error:
                raise ProviderError(
                    "The model returned an invalid analysis."
                ) from error
        values = payload.model_dump(exclude={"sources"})
        return AnalysisContent(**values, sources=[])
