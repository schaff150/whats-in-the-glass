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
    assert "secrets" not in service
    assert "WITG_SECRET_KEY_FILE" not in service["environment"]
    assert "WITG_ADMIN_PASSWORD_FILE" not in service["environment"]
    assert (
        service["environment"]["FORWARDED_ALLOW_IPS"]
        == "${WITG_TRUSTED_PROXY_IPS:-172.17.0.1}"
    )
    assert (
        service["environment"]["WITG_TRUSTED_PROXY_IPS"]
        == "${WITG_TRUSTED_PROXY_IPS:-172.17.0.1}"
    )


def test_secrets_compose_override_requires_external_paths_and_repo_ignores_secrets():
    override_text = (ROOT / "compose.secrets.yaml").read_text(encoding="utf-8")
    override = yaml.safe_load(override_text)
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    service = override["services"]["whats-in-the-glass"]
    assert (
        service["environment"]["WITG_SECRET_KEY_FILE"] == "/run/secrets/witg_master_key"
    )
    assert (
        service["environment"]["WITG_ADMIN_PASSWORD_FILE"]
        == "/run/secrets/witg_admin_password"
    )
    assert service["secrets"] == ["witg_master_key", "witg_admin_password"]
    assert "${WITG_SECRET_KEY_PATH:?" in override_text
    assert "${WITG_ADMIN_PASSWORD_PATH:?" in override_text
    assert "./secrets" not in override_text
    assert "secrets/" in gitignore.splitlines()


def test_crypto_dependency_uses_audited_major_version():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"cryptography>=50,<51"' in pyproject


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
    assert "WITG_TRUSTED_PROXY_IPS" in readme
    assert "FORWARDED_ALLOW_IPS" in readme
    assert "172.17.0.1" in readme
    assert "compose.secrets.yaml" in readme
    assert "WITG_PROVIDER_MAX_CONCURRENCY" in readme
    assert "WITG_ANALYSIS_RATE_LIMIT" in readme
    assert "resets when the process restarts" in readme
    assert "http://modelbox.home.arpa" in readme

    assert "AnalysisService" in architecture
    assert "PhotoInput" in architecture
    assert "SQLite" in architecture
    assert "WITG_DATA_DIR" in architecture
    assert "LAN-local" in architecture
    assert "network-first" in architecture
    assert "HTTPS" in architecture
    assert "validated IP" in architecture
    assert "TLS SNI" in architecture
    assert "monotonic" in architecture
    assert "single-flight" in architecture
    assert "trusted proxy" in architecture
    assert "172.17.0.1" in architecture
