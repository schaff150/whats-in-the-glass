import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimitExceeded(RuntimeError):
    pass


class PerClientRateLimiter:
    """In-process sliding-window limiter; state resets when the process restarts."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._limit = max(1, limit)
        self._window = max(0.001, window_seconds)
        self._monotonic = monotonic
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client_id: str) -> None:
        now = self._monotonic()
        with self._lock:
            events = self._events[client_id]
            while events and now - events[0] >= self._window:
                events.popleft()
            if len(events) >= self._limit:
                raise RateLimitExceeded
            events.append(now)
