import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import MAX_REQUEST_BYTES, create_api_router
from app.db import Database
from app.middleware import RequestBodyLimitMiddleware
from app.services.analysis import AnalysisService, DemoAnalysisService

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    data_dir: str | Path | None = None,
    analysis_service: AnalysisService | None = None,
) -> FastAPI:
    resolved_data_dir = Path(data_dir or os.environ.get("WITG_DATA_DIR", "data"))
    database = Database(resolved_data_dir / "witg.sqlite3")
    database.initialize()

    app = FastAPI(title="What’s in the Glass?")
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)
    app.state.data_dir = resolved_data_dir
    app.state.database = database
    app.state.analysis_service = analysis_service or DemoAnalysisService()
    app.include_router(create_api_router(database, app.state.analysis_service))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "service": "whats-in-the-glass"}

    @app.get("/service-worker.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "service-worker.js", media_type="text/javascript"
        )

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    return app


app = create_app()
