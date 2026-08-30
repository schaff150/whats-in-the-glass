import hmac

from fastapi import HTTPException, Request, status


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
