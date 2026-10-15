import threading

from crawler.rate_limiter import RateLimiter, acquire, parse_limit_header


def max_in_window(stamps: list[float], seconds: float) -> int:
    """Largest number of stamps in any half-open window (t - seconds, t]"""
    return max(sum(1 for s in stamps if t - seconds < s <= t) for t in stamps)


def test_burst_uses_full_limit_without_exceeding_it(clock):
    limiter = RateLimiter([(100, 120.0), (20, 1.0)], pad_seconds=1.0, clock=clock)
    stamps = []
    for _ in range(400):
        acquire([limiter], threading.Lock(), clock.sleep)
        stamps.append(clock.t)
    assert max_in_window(stamps, 121.0) <= 100
    assert max_in_window(stamps, 2.0) <= 20
    assert stamps[100] - stamps[0] == 121.0
    assert stamps[-1] - stamps[0] < 4 * 121


def test_combined_app_and_method_limits(clock):
    app = RateLimiter([(100, 120.0), (20, 1.0)], clock=clock)
    method = RateLimiter([(50, 10.0)], clock=clock)
    stamps = []
    for _ in range(200):
        acquire([app, method], threading.Lock(), clock.sleep)
        stamps.append(clock.t)
    assert max_in_window(stamps, 10.0) <= 50
    assert max_in_window(stamps, 120.0) <= 100
    assert max_in_window(stamps, 1.0) <= 20


def test_penalize_blocks_until_retry_after(clock):
    limiter = RateLimiter([(20, 1.0)], clock=clock)
    limiter.penalize(7.0)
    start = clock.t
    acquire([limiter], threading.Lock(), clock.sleep)
    assert clock.t - start >= 7.0


def test_sync_adopts_higher_server_count(clock):
    limiter = RateLimiter([(100, 120.0)], clock=clock)
    assert limiter.wait_time() == 0
    limiter.sync({120.0: 100})  # another process used the key
    assert limiter.wait_time() > 0


def test_sync_ignores_lower_server_count(clock):
    limiter = RateLimiter([(100, 120.0)], clock=clock)
    for _ in range(10):
        limiter.record()
    limiter.sync({120.0: 3})
    assert limiter._windows[120.0].count(clock.t) == 10


def test_configure_keeps_history_for_same_window(clock):
    limiter = RateLimiter([(100, 120.0), (20, 1.0)], clock=clock)
    for _ in range(100):
        limiter.record()
        clock.t += 0.1
    limiter.configure([(100, 120.0)])
    assert limiter.wait_time() > 0
    assert limiter.limits() == [(100, 120.0)]


def test_threads_sharing_a_lock_never_take_the_same_slot():
    limiter = RateLimiter([(100, 60.0)])
    lock, granted = threading.Lock(), []

    class Full(Exception):
        pass

    def full(_seconds):
        raise Full  # the window is full: stop this thread instead of waiting a minute

    def worker():
        try:
            for _ in range(50):
                acquire([limiter], lock, full)
                granted.append(1)
        except Full:
            pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 100


def test_apply_headers_adopts_riots_limits_and_counts(clock):
    limiter = RateLimiter([(1_200_000, 600.0)], clock=clock)
    limiter.apply_headers("20:1,100:120", "3:1,100:120")
    assert limiter.limits() == [(20, 1.0), (100, 120.0)]
    assert limiter.wait_time() > 0  # Riot says the 120 s window is already full


def test_parse_limit_header():
    assert parse_limit_header("100:120,20:1") == [(100, 120.0), (20, 1.0)]
    assert parse_limit_header(None) == []
