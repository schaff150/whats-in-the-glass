from fastapi.testclient import TestClient

from app.main import create_app


def test_security_headers_and_settings_no_store_without_admin_session(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, secret_key=b"s" * 32)) as client:
        home = client.get("/")
        settings = client.get("/api/settings")

    assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
    assert "'unsafe-inline'" not in home.headers["content-security-policy"]
    assert home.headers["x-content-type-options"] == "nosniff"
    assert home.headers["referrer-policy"] == "no-referrer"
    assert settings.status_code == 200
    assert settings.headers["cache-control"] == "no-store"
