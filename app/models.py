from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
