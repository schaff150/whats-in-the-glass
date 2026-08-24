import pytest
from pydantic import ValidationError

from app.db import Database
from app.models import AnalysisCreate, FeedbackCreate


def analysis_input(title: str = "Glenrothes 11") -> AnalysisCreate:
    return AnalysisCreate(
        drinker="Me",
        guest_name=None,
        submitted_drink_name="Gordon & MacPhail Discovery Glenrothes 11",
        identified_title=title,
        region="Speyside",
        age_statement="11 years",
        abv="43%",
        style="Sherry-matured single malt",
        overview="The bottler matters nearly as much as the distillery.",
        context="A clearly labelled demonstration briefing.",
        tasting_notes="Toffee, orchard fruit and orange peel.",
        sources=["Gordon & MacPhail — official product information"],
    )


def test_initializes_schema_and_round_trips_analysis(tmp_path):
    database = Database(tmp_path / "witg.sqlite3")
    database.initialize()

    saved = database.save_analysis(analysis_input())
    loaded = database.get_analysis(saved.id)

    assert saved.id > 0
    assert loaded == saved
    assert loaded.identified_title == "Glenrothes 11"
    assert loaded.sources == ["Gordon & MacPhail — official product information"]
    assert loaded.created_at.tzinfo is not None


def test_saves_rating_and_note_without_executing_note_as_sql(tmp_path):
    database = Database(tmp_path / "witg.sqlite3")
    database.initialize()
    saved = database.save_analysis(analysis_input())
    malicious_note = "Lovely'); DROP TABLE analyses; --"

    updated = database.save_feedback(
        saved.id, FeedbackCreate(rating=5, notes=malicious_note)
    )

    assert updated.rating == 5
    assert updated.notes == malicious_note
    assert database.get_analysis(saved.id).identified_title == "Glenrothes 11"


def test_lists_newest_analyses_first_with_feedback(tmp_path):
    database = Database(tmp_path / "witg.sqlite3")
    database.initialize()
    older = database.save_analysis(analysis_input("Older bottle"))
    database.save_feedback(older.id, FeedbackCreate(rating=4, notes="Would pour again"))
    newer = database.save_analysis(analysis_input("Newer bottle"))

    history = database.list_analyses()

    assert [item.id for item in history] == [newer.id, older.id]
    assert history[0].rating is None
    assert history[1].rating == 4
    assert history[1].notes == "Would pour again"


@pytest.mark.parametrize("rating", [0, 6])
def test_rejects_rating_outside_one_to_five(rating):
    with pytest.raises(ValidationError):
        FeedbackCreate(rating=rating, notes=None)
