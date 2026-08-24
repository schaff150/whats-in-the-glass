from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.db import Database
from app.models import (
    AnalysisCreate,
    AnalysisRecord,
    Drinker,
    FeedbackCreate,
    FeedbackRecord,
)
from app.services.analysis import AnalysisService

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def create_api_router(
    database: Database, analysis_service: AnalysisService
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post(
        "/analyses",
        response_model=AnalysisRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def create_analysis(
        drinker: Annotated[Drinker, Form()],
        drink_name: Annotated[str | None, Form()] = None,
        guest_name: Annotated[str | None, Form()] = None,
        photo: Annotated[UploadFile | None, File()] = None,
    ) -> AnalysisRecord:
        drink_name = drink_name.strip() if drink_name else None
        guest_name = guest_name.strip() if drinker == "Other" and guest_name else None
        if not drink_name and photo is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Provide a drink name or photograph.",
            )
        if photo is not None and photo.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Photographs must be JPEG, PNG, or WebP images.",
            )
        if (
            photo is not None
            and len(photo.file.read(MAX_IMAGE_BYTES + 1)) > MAX_IMAGE_BYTES
        ):
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Photographs must be no larger than 5 MiB.",
            )
        content = analysis_service.analyze(
            drink_name, photo_supplied=photo is not None
        )
        return database.save_analysis(
            AnalysisCreate(
                drinker=drinker,
                guest_name=guest_name,
                submitted_drink_name=drink_name,
                **content.__dict__,
            )
        )

    @router.post("/analyses/{analysis_id}/feedback", response_model=FeedbackRecord)
    def save_feedback(
        analysis_id: int, feedback: FeedbackCreate
    ) -> FeedbackRecord:
        if database.get_analysis(analysis_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Analysis not found.",
            )
        return database.save_feedback(analysis_id, feedback)

    @router.get("/history", response_model=list[AnalysisRecord])
    def history() -> list[AnalysisRecord]:
        return database.list_analyses()

    return router
