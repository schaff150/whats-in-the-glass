from fastapi.testclient import TestClient

from app.main import create_app


def test_connections_need_no_admin_password_but_do_not_expose_keys(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, secret_key=b"m" * 32)) as client:
        response = client.get("/api/settings")

    assert response.status_code == 200
    settings = response.json()
    assert settings["model"]["api_key_configured"] is False
    assert settings["voice"]["api_key_configured"] is False


def test_connection_mutations_remain_same_origin_and_write_only(tmp_path):
    payload = {
        "provider": "openai_compatible",
        "display_name": "Local model",
        "base_url": "https://api.openai.com/v1",
        "model_id": "test-model",
        "api_key": "unit-test-secret",
        "clear_api_key": False,
    }
    with TestClient(create_app(data_dir=tmp_path, secret_key=b"m" * 32)) as client:
        rejected = client.put("/api/settings/model", json=payload)
        accepted = client.put(
            "/api/settings/model",
            json=payload,
            headers={"Origin": "http://testserver"},
        )
        settings = client.get("/api/settings")

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert "unit-test-secret" not in accepted.text
    assert settings.json()["model"]["api_key_configured"] is True


def test_removed_admin_routes_are_not_available(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, secret_key=b"m" * 32)) as client:
        response = client.post("/api/admin/login", json={"password": "unused"})

    assert response.status_code == 404
