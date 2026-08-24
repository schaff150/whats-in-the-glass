import asyncio

from app.middleware import RequestBodyLimitMiddleware


def http_scope(path: str = "/api/analyses") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }


def test_streamed_oversize_request_is_rejected_before_downstream_response():
    downstream_called = False
    sent = []
    messages = iter(
        [
            {"type": "http.request", "body": b"a" * 6, "more_body": True},
            {"type": "http.request", "body": b"b" * 6, "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        nonlocal downstream_called
        downstream_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=10)
    asyncio.run(middleware(http_scope(), receive, send))

    assert downstream_called is False
    response_starts = [
        message for message in sent if message["type"] == "http.response.start"
    ]
    assert [message["status"] for message in response_starts] == [413]


def test_streamed_request_is_replayed_to_downstream_after_bounded_buffering():
    sent = []
    received_body = None
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        nonlocal received_body
        request = await receive()
        received_body = request["body"]
        assert request["more_body"] is False
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=10)
    asyncio.run(middleware(http_scope("/api/analyses/1/feedback"), receive, send))

    assert received_body == b"abcdef"
    response_starts = [
        message for message in sent if message["type"] == "http.response.start"
    ]
    assert [message["status"] for message in response_starts] == [200]
