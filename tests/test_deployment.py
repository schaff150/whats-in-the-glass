from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_container_contract_is_non_root_persistent_and_healthy_without_curl():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert "USER witg" in dockerfile
    assert "EXPOSE 8787" in dockerfile
    assert "WITG_DATA_DIR=/data" in dockerfile
    assert "urllib.request" in dockerfile
    assert "curl" not in dockerfile.lower()

    service = compose["services"]["whats-in-the-glass"]
    assert service["ports"] == ["8787:8787"]
    assert service["environment"]["WITG_DATA_DIR"] == "/data"
    assert service["volumes"] == ["witg-data:/data"]
    assert "python" in service["healthcheck"]["test"]
    assert "curl" not in str(service["healthcheck"]).lower()
    assert "witg-data" in compose["volumes"]


def test_operator_and_architecture_docs_cover_runbook_and_demo_boundaries():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")

    for command in [
        "uv sync",
        "uv run pytest -q",
        "uv run uvicorn app.main:app",
        "docker compose up --build",
    ]:
        assert command in readme
    assert "http://localhost:8787/api/health" in readme
    assert "Docker is not installed on Dadbase" in readme
    assert "no real model" in readme.lower()
    assert "ElevenLabs" in readme
    assert "HTTPS" in readme

    assert "AnalysisService" in architecture
    assert "PhotoInput" in architecture
    assert "SQLite" in architecture
    assert "WITG_DATA_DIR" in architecture
    assert "LAN-local" in architecture
    assert "network-first" in architecture
    assert "HTTPS" in architecture
