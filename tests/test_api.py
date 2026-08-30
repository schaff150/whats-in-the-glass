from fastapi.testclient import TestClient

from app.main import create_app
from app.services.analysis import AnalysisContent


def test_text_analysis_returns_and_persists_approved_demo(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses",
            data={
                "drinker": "Me",
                "drink_name": "Gordon & MacPhail Discovery Glenrothes 11",
            },
        )

    assert response.status_code == 201
    analysis = response.json()
    assert analysis["id"] > 0
    assert analysis["drinker"] == "Me"
    assert analysis["submitted_drink_name"] == (
        "Gordon & MacPhail Discovery Glenrothes 11"
    )
    assert analysis["identified_title"] == ("Gordon & MacPhail Discovery Glenrothes 11")
    assert "demonstration" in analysis["context"].lower()


def test_unknown_text_is_labelled_as_a_demonstration(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses",
            data={"drinker": "Wife", "drink_name": "Mystery Garden Gin"},
        )

    assert response.status_code == 201
    analysis = response.json()
    assert analysis["identified_title"] == "Mystery Garden Gin"
    assert analysis["region"] == "Not verified in demo"
    assert "demonstration" in analysis["overview"].lower()
    assert "does not identify or research" in analysis["context"].lower()


def test_photo_only_analysis_never_invents_a_bottle_identity(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses",
            data={"drinker": "Other", "guest_name": "Jamie"},
            files={
                "photo": (
                    "label.webp",
                    b"RIFF\x04\x00\x00\x00WEBPdemo",
                    "image/webp",
                )
            },
        )

    assert response.status_code == 201
    analysis = response.json()
    assert analysis["drinker"] == "Other"
    assert analysis["guest_name"] == "Jamie"
    assert analysis["submitted_drink_name"] is None
    assert analysis["identified_title"] == "Bottle photograph — identification pending"
    assert "no image recognition" in analysis["overview"].lower()
    assert analysis["sources"] == []


def test_empty_analysis_submission_is_rejected(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post("/api/analyses", data={"drinker": "Me"})

    assert response.status_code == 422
    assert "drink name or photograph" in response.json()["detail"].lower()


def test_non_image_upload_is_rejected(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses",
            data={"drinker": "Me"},
            files={"photo": ("notes.pdf", b"%PDF", "application/pdf")},
        )

    assert response.status_code == 415
    assert "jpeg, png, or webp" in response.json()["detail"].lower()


def test_empty_and_spoofed_image_uploads_are_rejected(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        empty = client.post(
            "/api/analyses",
            data={"drinker": "Me"},
            files={"photo": ("empty.jpg", b"", "image/jpeg")},
        )
        spoofed = client.post(
            "/api/analyses",
            data={"drinker": "Me"},
            files={"photo": ("fake.png", b"not-a-png", "image/png")},
        )

    assert empty.status_code == 415
    assert spoofed.status_code == 415
    assert "valid image" in spoofed.json()["detail"].lower()


def test_request_body_limit_rejects_upload_before_route_file_limit(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses",
            data={"drinker": "Me"},
            files={
                "photo": (
                    "huge.jpg",
                    b"\xff\xd8\xff" + b"x" * (6 * 1024 * 1024),
                    "image/jpeg",
                )
            },
        )

    assert response.status_code == 413
    assert "request body" in response.json()["detail"].lower()


def test_request_body_limit_covers_other_api_writes(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses/1/feedback",
            content=b" " * (6 * 1024 * 1024),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert "request body" in response.json()["detail"].lower()


def test_oversized_image_is_rejected(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses",
            data={"drinker": "Me"},
            files={"photo": ("label.jpg", b"x" * (5 * 1024 * 1024 + 1), "image/jpeg")},
        )

    assert response.status_code == 413
    assert "5 mib" in response.json()["detail"].lower()


def test_analysis_text_fields_have_conservative_length_limits(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        long_drink = client.post(
            "/api/analyses",
            data={"drinker": "Me", "drink_name": "x" * 301},
        )
        long_guest = client.post(
            "/api/analyses",
            data={
                "drinker": "Other",
                "guest_name": "x" * 81,
                "drink_name": "House pour",
            },
        )

    assert long_drink.status_code == 422
    assert long_guest.status_code == 422


def test_feedback_note_has_a_conservative_length_limit(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        created = client.post(
            "/api/analyses",
            data={"drinker": "Me", "drink_name": "House pour"},
        ).json()
        response = client.post(
            f"/api/analyses/{created['id']}/feedback",
            json={"rating": 4, "notes": "x" * 1001},
        )

    assert response.status_code == 422


def test_feedback_endpoint_persists_rating_and_notes(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/analyses",
            data={"drinker": "Me", "drink_name": "Mystery Garden Gin"},
        ).json()
        response = client.post(
            f"/api/analyses/{created['id']}/feedback",
            json={"rating": 4, "notes": "Bright citrus, dry finish"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "analysis_id": created["id"],
        "rating": 4,
        "notes": "Bright citrus, dry finish",
    }
    stored = app.state.database.list_analyses()[0]
    assert stored.rating == 4
    assert stored.notes == "Bright citrus, dry finish"


def test_history_endpoint_lists_newest_first_with_feedback(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        older = client.post(
            "/api/analyses",
            data={"drinker": "Me", "drink_name": "First pour"},
        ).json()
        client.post(
            f"/api/analyses/{older['id']}/feedback",
            json={"rating": 3, "notes": "Pleasant"},
        )
        newer = client.post(
            "/api/analyses",
            data={"drinker": "Wife", "drink_name": "Second pour"},
        ).json()

        response = client.get("/api/history")

    assert response.status_code == 200
    history = response.json()
    assert [item["id"] for item in history] == [newer["id"], older["id"]]
    assert history[1]["rating"] == 3
    assert history[1]["notes"] == "Pleasant"


def test_history_endpoint_is_bounded_and_paginated(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        created = [
            client.post(
                "/api/analyses",
                data={"drinker": "Me", "drink_name": f"Pour {number}"},
            ).json()
            for number in range(3)
        ]
        page = client.get("/api/history?limit=2&offset=1")
        too_large = client.get("/api/history?limit=101")
        too_far = client.get("/api/history?offset=10001")

    assert page.status_code == 200
    assert [item["id"] for item in page.json()] == [created[1]["id"], created[0]["id"]]
    assert too_large.status_code == 422
    assert too_far.status_code == 422


def test_guest_name_is_discarded_unless_drinker_is_other(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses",
            data={
                "drinker": "Me",
                "guest_name": "Should not persist",
                "drink_name": "House pour",
            },
        )

    assert response.status_code == 201
    assert response.json()["guest_name"] is None


def test_feedback_for_unknown_analysis_is_rejected(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post(
            "/api/analyses/999/feedback",
            json={"rating": 5, "notes": "Impossible"},
        )

    assert response.status_code == 404
    assert "analysis not found" in response.json()["detail"].lower()


def test_analysis_service_can_be_replaced_at_app_boundary(tmp_path):
    class ReplacementService:
        def analyze(self, drinker, drink_name, photo):
            return AnalysisContent(
                identified_title="Replacement result",
                region="Test region",
                age_statement="Test age",
                abv="Test ABV",
                style="Test style",
                overview="Replacement overview",
                context="Replacement context",
                tasting_notes="Replacement tasting notes",
                sources=["Replacement source"],
            )

    app = create_app(data_dir=tmp_path, analysis_service=ReplacementService())
    with TestClient(app) as client:
        response = client.post(
            "/api/analyses",
            data={"drinker": "Me", "drink_name": "Anything"},
        )

    assert response.status_code == 201
    assert response.json()["identified_title"] == "Replacement result"


def test_analysis_service_receives_uploaded_photo_content(tmp_path):
    captured = {}

    class ReplacementService:
        def analyze(self, drinker, drink_name, photo):
            captured["drinker"] = drinker
            captured["drink_name"] = drink_name
            captured["photo"] = photo
            return AnalysisContent(
                identified_title="Photo result",
                region="Test region",
                age_statement="Test age",
                abv="Test ABV",
                style="Test style",
                overview="Replacement overview",
                context="Replacement context",
                tasting_notes="Replacement tasting notes",
                sources=[],
            )

    app = create_app(data_dir=tmp_path, analysis_service=ReplacementService())
    with TestClient(app) as client:
        response = client.post(
            "/api/analyses",
            data={"drinker": "Me"},
            files={
                "photo": (
                    "label.png",
                    b"\x89PNG\r\n\x1a\nimage-bytes",
                    "image/png",
                )
            },
        )

    assert response.status_code == 201
    assert captured["drinker"] == "Me"
    assert captured["drink_name"] is None
    assert captured["photo"].content == b"\x89PNG\r\n\x1a\nimage-bytes"
    assert captured["photo"].content_type == "image/png"
    assert captured["photo"].filename == "label.png"
