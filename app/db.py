import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.models import AnalysisCreate, AnalysisRecord, FeedbackCreate, FeedbackRecord


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
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
        return AnalysisRecord(id=analysis_id, created_at=created_at, **analysis.model_dump())

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

    def list_analyses(self) -> list[AnalysisRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT analyses.*, feedback.rating, feedback.notes
                FROM analyses
                LEFT JOIN feedback ON feedback.analysis_id = analyses.id
                ORDER BY analyses.created_at DESC, analyses.id DESC
                """
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
        values["sources"] = json.loads(values.pop("sources_json"))
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        return AnalysisRecord.model_validate(values)
