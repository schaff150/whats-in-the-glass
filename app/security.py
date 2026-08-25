import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

COOKIE_NAME = "witg_admin_session"
SESSION_SECONDS = 30 * 60
MAX_LOGIN_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 5 * 60
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


@dataclass(frozen=True)
class AdminSession:
    csrf_token: str
    expires_at: int


class AdminConfigurationError(RuntimeError):
    pass


class AdminAuth:
    def __init__(self, password_record: str | None, master_key: str | bytes | None):
        self._password_record = password_record
        self._key = self._normalize_key(master_key)
        self._sessions: dict[str, AdminSession] = {}
        self._attempts: dict[str, list[float]] = {}

    @staticmethod
    def _normalize_key(value: str | bytes | None) -> bytes | None:
        if not value:
            return None
        raw = value.encode() if isinstance(value, str) else value
        if len(raw) == 32:
            return raw
        try:
            decoded = base64.urlsafe_b64decode(raw)
        except (ValueError, TypeError):
            return None
        return decoded if len(decoded) == 32 else None

    @property
    def configured(self) -> bool:
        return bool(self._password_record and self._key)

    def login(self, password: str, client_id: str) -> tuple[str, AdminSession]:
        if not self.configured:
            raise AdminConfigurationError(
                "Admin access is unavailable until credential files are configured."
            )
        now = time.time()
        attempts = [
            attempt
            for attempt in self._attempts.get(client_id, [])
            if now - attempt < ATTEMPT_WINDOW_SECONDS
        ]
        if len(attempts) >= MAX_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
            )
        if not verify_admin_password(password, self._password_record or ""):
            attempts.append(now)
            self._attempts[client_id] = attempts
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid admin credentials.",
            )
        self._attempts.pop(client_id, None)
        session_id = secrets.token_urlsafe(24)
        session = AdminSession(
            csrf_token=secrets.token_urlsafe(24),
            expires_at=int(now) + SESSION_SECONDS,
        )
        self._sessions[session_id] = session
        payload = f"{session_id}.{session.expires_at}"
        signature = (
            base64.urlsafe_b64encode(
                hmac.new(self._key, payload.encode(), hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        return f"{payload}.{signature}", session

    def validate(self, token: str | None) -> tuple[str, AdminSession] | None:
        if not token or not self._key:
            return None
        try:
            session_id, expires, signature = token.split(".", 2)
            expires_at = int(expires)
        except (ValueError, TypeError):
            return None
        payload = f"{session_id}.{expires_at}"
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(self._key, payload.encode(), hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        if not hmac.compare_digest(signature, expected) or expires_at < int(
            time.time()
        ):
            self._sessions.pop(session_id, None)
            return None
        session = self._sessions.get(session_id)
        if session is None or session.expires_at != expires_at:
            return None
        return session_id, session

    def logout(self, token: str | None) -> None:
        validated = self.validate(token)
        if validated:
            self._sessions.pop(validated[0], None)


def request_is_https(request: Request) -> bool:
    return request.url.scheme == "https"


def validate_exact_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin validation failed.",
        )
    scheme = "https" if request_is_https(request) else request.url.scheme
    expected = f"{scheme}://{request.headers.get('host', '')}"
    if not hmac.compare_digest(origin.rstrip("/"), expected.rstrip("/")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin validation failed.",
        )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_admin_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return (
        f"scrypt$v=1$n={SCRYPT_N}$r={SCRYPT_R}$p={SCRYPT_P}"
        f"$salt={_b64encode(salt)}$hash={_b64encode(digest)}"
    )


def verify_admin_password(password: str, record: str) -> bool:
    if len(record) > 1024:
        return False
    try:
        parts = record.split("$")
        if len(parts) != 7 or parts[:2] != ["scrypt", "v=1"]:
            return False
        values = dict(part.split("=", 1) for part in parts[2:])
        if len(values) != 5 or set(values) != {"n", "r", "p", "salt", "hash"}:
            return False
        n, r, p = int(values["n"]), int(values["r"]), int(values["p"])
        if (
            n < 2**14
            or n > 2**18
            or n & (n - 1)
            or not 1 <= r <= 16
            or not 1 <= p <= 4
            or 128 * n * r > 256 * 1024 * 1024
            or p * r > 64
        ):
            return False
        salt = _b64decode(values["salt"])
        expected = _b64decode(values["hash"])
        if not 16 <= len(salt) <= 64 or not 32 <= len(expected) <= 64:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError, MemoryError):
        return False
