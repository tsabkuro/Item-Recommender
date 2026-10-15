"""Sliding-window rate limiter for Riot's "N requests per W seconds" limits

Riot has multiple windows per key AND per method (e.g. 2000/10s) and every request
has to fit inside all of them

We try to use the full limit but treat every window as being `pad_seconds` longer than it is
due to Riot counting request on arrival, and network jitter could cause more requests to be landed in Riots window
"""
import logging
import threading
import time
from collections import deque
from typing import Callable, Iterable, Sequence

log = logging.getLogger(__name__)

Limits = Sequence[tuple[int, float]]  # [(max_requests, window_seconds), ...]


class _Window:
    def __init__(self, limit: int, seconds: float, pad_seconds: float):
        self.limit = limit # max requests in the last x seconds (window size)
        self.seconds = seconds
        self.span = seconds + pad_seconds
        self.time_stamps: deque[float] = deque()

    def _evict(self, now: float) -> None:
        while self.time_stamps and self.time_stamps[0] <= now - self.span:
            self.time_stamps.popleft()

    def wait_time(self, now: float) -> float:
        self._evict(now)
        if len(self.time_stamps) < self.limit:
            return 0.0
        # wait until enough old stamps age out to free one slot
        oldest_blocking = self.time_stamps[len(self.time_stamps) - self.limit] # just in case
        return oldest_blocking + self.span - now

    def record(self, now: float) -> None:
        self.time_stamps.append(now)

    def count(self, now: float) -> int:
        self._evict(now)
        return len(self.time_stamps)


class RateLimiter:
    def __init__(self, limits: Limits, pad_seconds: float = 0.0, clock: Callable[[], float] = time.monotonic):
        self.pad_seconds = pad_seconds
        self.clock = clock
        self.blocked_until = 0.0
        self._windows: dict[float, _Window] = {}
        self.configure(limits)

    def configure(self, limits: Limits) -> None:
        """Set the windows, and keep existing history unchanged"""
        new = {}
        for limit, seconds in limits:
            window = _Window(limit, seconds, self.pad_seconds)
            if seconds in self._windows:
                window.time_stamps = self._windows[seconds].time_stamps
            new[seconds] = window
        self._windows = new

    def limits(self) -> list[tuple[int, float]]:
        return sorted((w.limit, w.seconds) for w in self._windows.values())

    def wait_time(self) -> float:
        now = self.clock()
        waits = [w.wait_time(now) for w in self._windows.values()]
        return max([self.blocked_until - now, 0.0, *waits])

    def record(self) -> None:
        now = self.clock()
        for w in self._windows.values():
            w.record(now)

    def penalize(self, seconds: float) -> None:
        """Block all requests for `x` seconds (used for 429 Retry-After)"""
        self.blocked_until = max(self.blocked_until, self.clock() + seconds)

    def sync(self, server_counts: dict[float, int]) -> None:
        """
        Sync server counts with ours to cover requests we didn't see
        """
        now = self.clock()
        for seconds, server_count in server_counts.items():
            window = self._windows.get(seconds)
            if window is None:
                continue
            for _ in range(server_count - window.count(now)):
                window.record(now)

    def apply_headers(self, limit_header: str | None, count_header: str | None) -> None:
        """Adopt the limits and counts from headers"""
        limits = parse_limit_header(limit_header)
        if limits and sorted(limits) != self.limits():
            log.info("Rate limits updated from headers: %s", limit_header)
            self.configure(limits)
        self.sync({seconds: count for count, seconds in parse_limit_header(count_header)})


def acquire(limiters: Iterable[RateLimiter], lock: threading.Lock,
            sleep: Callable[[float], None] = time.sleep) -> None:
    """
    Make sure every limiter has room, and record new timestamp

    Use lock for multithreading, if workers share the same limiter
    """
    limiters = list(limiters)
    while True:
        with lock:
            wait = max(limiter.wait_time() for limiter in limiters)
            if wait <= 0:
                for limiter in limiters:
                    limiter.record()
                return
        sleep(max(wait, 0.001)) # in case rounding error


def parse_limit_header(value: str | None) -> list[tuple[int, float]]:
    """
    Parse "2000:10,1200000:600" into [(2000, 10.0), (1200000, 600.0)]
    
    (count, seconds) format
    """
    if not value:
        return []
    pairs = []
    for part in value.split(","):
        count, seconds = part.split(":")
        pairs.append((int(count), float(seconds)))
    return pairs
