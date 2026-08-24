from dataclasses import dataclass
from typing import Protocol


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


class AnalysisService(Protocol):
    def analyze(self, drink_name: str | None, photo_supplied: bool) -> AnalysisContent: ...


class DemoAnalysisService:
    """Deterministic placeholder; it performs no AI or web research."""

    def analyze(self, drink_name: str | None, photo_supplied: bool) -> AnalysisContent:
        if photo_supplied and not drink_name:
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
