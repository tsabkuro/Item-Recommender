"""Riot API Client with rate limiting and retry

A single region worker's have a shared client, so the rate limiter and connection pool shared
"""
import logging
import threading
import time
from urllib.parse import quote

import requests
from dotenv import dotenv_values
from requests.adapters import HTTPAdapter

from crawler import config
from crawler.rate_limiter import RateLimiter, acquire

log = logging.getLogger(__name__)


class RiotApiError(Exception):
    pass


class NotFound(RiotApiError):
    pass


class BadRequest(RiotApiError):
    pass


class ApiKeyRejected(Exception):
    """Riot refused the key (401/403), so stop crawling"""


class RiotClient:
    def __init__(self, region: config.Region):
        self.region = region
        self.api_key = dotenv_values(config.ENV_FILE).get("RIOT_API_KEY")

        if not self.api_key:
            raise ApiKeyRejected(f"RIOT_API_KEY missing from {config.ENV_FILE}")

        self.session = requests.Session()
        # keep one connection per worker for each host
        self.session.mount("https://", HTTPAdapter(pool_maxsize=config.WORKERS_PER_REGION))
        self._limiters_lock = threading.Lock()
        self._app_limiters = {
            route: RateLimiter(config.APP_LIMITS, config.RATE_WINDOW_PAD_S, time.monotonic)
            for route in (region.platform, region.regional)
        }
        # keys are all (route, method) e.g. ("na1", "league-v4.entries")
        self._method_limiters: dict[tuple[str, str], RateLimiter] = {}
        self._probe_locks: dict[tuple[str, str], threading.Lock] = {}
        self._heard: set[tuple[str, str]] = set()
        self.requests_made = 0
        self.throttled = 0  # 429s received; should stay ~0 at full rate

    def _method_limiter(self, route: str, method: str) -> RateLimiter:
        key = (route, method)
        with self._limiters_lock:
            if key not in self._method_limiters:
                limits = config.METHOD_LIMITS.get(method, config.OTHER_METHOD_LIMITS)
                self._method_limiters[key] = RateLimiter(limits, config.RATE_WINDOW_PAD_S, time.monotonic)
                self._probe_locks[key] = threading.Lock()
            return self._method_limiters[key]

    def _get(self, route: str, method: str, path: str, params: dict | None = None, raw: bool = False):
        self._method_limiter(route, method)
        key = (route, method)
        if key not in self._heard:
            # probe to find method's real limits given from Riot's headers
            with self._probe_locks[key]:
                if key not in self._heard:
                    return self._request(route, method, path, params, raw)
        return self._request(route, method, path, params, raw)

    def _request(self, route: str, method: str, path: str, params: dict | None, raw: bool):
        url = f"https://{route}.api.riotgames.com{path}"
        app, meth = self._app_limiters[route], self._method_limiter(route, method)
        failures = 0  # http failures. give up after MAX_REQUEST_FAILURES. use exponential backoff
        offline = 0   # network failures. use exponential backoff
        while True:
            acquire((app, meth), self._limiters_lock, time.sleep)
            with self._limiters_lock:
                self.requests_made += 1
            try:
                resp = self.session.get(url, params=params, headers={"X-Riot-Token": self.api_key}, timeout=15)
            except requests.RequestException as exc:
                # network failure, just wait for network
                offline += 1
                delay = min(60, 2 ** offline)
                log.warning("%s network error (%s), retrying in %ds", method, type(exc).__name__, delay)
                time.sleep(delay)
                continue
            offline = 0

            h = resp.headers
            with self._limiters_lock:
                app.apply_headers(h.get("X-App-Rate-Limit"), h.get("X-App-Rate-Limit-Count"))
                meth.apply_headers(h.get("X-Method-Rate-Limit"), h.get("X-Method-Rate-Limit-Count"))
                if h.get("X-Method-Rate-Limit"):
                    self._heard.add((route, method))

            status = resp.status_code
            if status == 200:
                return resp.content if raw else resp.json()
            if status == 429:
                with self._limiters_lock:
                    self.throttled += 1
                limit_type = h.get("X-Rate-Limit-Type", "unknown")
                retry_after = h.get("Retry-After")
                if retry_after is None:
                    # if 429s came without retry-after we treat like 5xx
                    failures += 1
                    if failures >= config.MAX_REQUEST_FAILURES:
                        raise RiotApiError(f"{method} {path}: repeated 429 ({limit_type})")
                    time.sleep(min(60, 2 ** failures))
                    continue
                log.warning("429 on %s (%s limit), backing off %ss; app count %s, method count %s",
                    method, limit_type, retry_after, h.get("X-App-Rate-Limit-Count"), h.get("X-Method-Rate-Limit-Count"))
                with self._limiters_lock:
                    (app if limit_type == "application" else meth).penalize(float(retry_after))
                continue
            if status in (401, 403):
                raise ApiKeyRejected(f"{method}: HTTP {status}, RIOT_API_KEY in {config.ENV_FILE} was rejected")
            if status == 404:
                raise NotFound(f"{method} {path}")
            if status == 400:
                raise BadRequest(f"{method} {path}: {resp.text[:200]}")
            if status >= 500:
                failures += 1
                if failures >= config.MAX_REQUEST_FAILURES:
                    raise RiotApiError(f"{method} {path}: HTTP {status}")
                log.warning("%s %s HTTP %d, retry %d", method, path, status, failures)
                time.sleep(min(60, 2 ** failures))
                continue
            raise RiotApiError(f"{method} {path}: HTTP {status}: {resp.text[:200]}")

    def league_entries(self, tier: str, division: str, page: int) -> list[dict]:
        path = f"/lol/league/v4/entries/{config.QUEUE_TYPE}/{tier}/{division}"
        return self._get(self.region.platform, "league-v4.entries", path, {"page": page})

    def match_ids(self, puuid: str, start_time: int, count: int, ranked_solo_only: bool = True) -> list[str]:
        path = f"/lol/match/v5/matches/by-puuid/{puuid}/ids"
        params = {"startTime": start_time, "count": count}
        if ranked_solo_only:
            params |= {"queue": config.QUEUE_ID, "type": "ranked"}
        return self._get(self.region.regional, "match-v5.ids", path, params)

    def account_puuid(self, game_name: str, tag_line: str) -> str:
        path = f"/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
        return self._get(self.region.regional, "account-v1.by-riot-id", path)["puuid"]

    # Match and timeline return the raw JSON body
    def match(self, match_id: str) -> bytes:
        return self._get(self.region.regional, "match-v5.match", f"/lol/match/v5/matches/{match_id}", raw=True)

    def timeline(self, match_id: str) -> bytes:
        return self._get(self.region.regional, "match-v5.timeline", f"/lol/match/v5/matches/{match_id}/timeline", raw=True)
