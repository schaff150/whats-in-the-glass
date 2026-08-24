from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_service_status(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "whats-in-the-glass"}


def test_root_serves_html(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "What’s in the Glass?" in response.text
