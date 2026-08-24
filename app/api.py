from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.db import Database
from app.models import (
    AnalysisCreate,
    AnalysisRecord,
    Drinker,
    FeedbackCreate,
    FeedbackRecord,
)
from app.services.analysis import AnalysisService, PhotoInput

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_IMAGE_BYTES + 512 * 1024


def has_valid_image_signature(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    return False


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
        drink_name: Annotated[str | None, Form(max_length=300)] = None,
        guest_name: Annotated[str | None, Form(max_length=80)] = None,
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
        photo_input = None
        if photo is not None:
            photo_bytes = photo.file.read(MAX_IMAGE_BYTES + 1)
            if len(photo_bytes) > MAX_IMAGE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Photographs must be no larger than 5 MiB.",
                )
            if not has_valid_image_signature(photo.content_type or "", photo_bytes):
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Photographs must contain valid image data.",
                )
            photo_input = PhotoInput(
                filename=photo.filename,
                content_type=photo.content_type or "",
                content=photo_bytes,
            )
        content = analysis_service.analyze(drink_name, photo=photo_input)
        return database.save_analysis(
            AnalysisCreate(
                drinker=drinker,
                guest_name=guest_name,
                submitted_drink_name=drink_name,
                **content.__dict__,
            )
        )

    @router.post("/analyses/{analysis_id}/feedback", response_model=FeedbackRecord)
    def save_feedback(analysis_id: int, feedback: FeedbackCreate) -> FeedbackRecord:
        if database.get_analysis(analysis_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Analysis not found.",
            )
        return database.save_feedback(analysis_id, feedback)

    @router.get("/history", response_model=list[AnalysisRecord])
    def history(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> list[AnalysisRecord]:
        return database.list_analyses(limit=limit, offset=offset)

    return router
