from fastapi.testclient import TestClient

from app.main import create_app
from app.security import hash_admin_password, verify_admin_password


def test_scrypt_admin_record_verifies_without_storing_plaintext():
    password = "unit-test-admin-passphrase-canary"
    record = hash_admin_password(password, salt=b"0123456789abcdef")

    assert password not in record
    assert verify_admin_password(password, record) is True
    assert verify_admin_password("wrong-passphrase", record) is False


def test_scrypt_admin_record_rejects_malformed_and_oversized_parameters_safely():
    valid = hash_admin_password("bounded-test-passphrase", salt=b"fedcba9876543210")
    oversized_n = valid.replace("n=16384", "n=1048576")
    duplicate = valid.replace("$r=8", "$n=16384$r=8")

    assert verify_admin_password("bounded-test-passphrase", "not-a-record") is False
    assert verify_admin_password("bounded-test-passphrase", oversized_n) is False
    assert verify_admin_password("bounded-test-passphrase", duplicate) is False
    assert verify_admin_password("bounded-test-passphrase", "x" * 1025) is False


def test_security_headers_are_applied_to_html_and_settings_are_no_store(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        secret_key=b"s" * 32,
        admin_password_record=hash_admin_password("headers-test-passphrase"),
        allow_insecure_credentials=True,
    )
    with TestClient(app) as client:
        home = client.get("/")
        login = client.post(
            "/api/admin/login",
            json={"password": "headers-test-passphrase"},
            headers={"Origin": "http://testserver"},
        )
        settings = client.get("/api/settings")

    assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
    assert "'unsafe-inline'" not in home.headers["content-security-policy"]
    assert home.headers["x-content-type-options"] == "nosniff"
    assert home.headers["referrer-policy"] == "no-referrer"
    assert login.headers["cache-control"] == "no-store"
    assert settings.headers["cache-control"] == "no-store"
