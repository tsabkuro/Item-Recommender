import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from crawler.config import REGIONS
from crawler import config, riot_client
from crawler.riot_client import ApiKeyRejected, BadRequest, NotFound, RiotApiError, RiotClient


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body

    @property
    def content(self):
        return json.dumps(self._body).encode()


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "key": headers["X-Riot-Token"]})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp .env in place of the real one."""
    path = tmp_path / ".env"
    path.write_text("RIOT_API_KEY=RGAPI-test\n")
    monkeypatch.setattr(config, "ENV_FILE", path)
    return path


@pytest.fixture(autouse=True)
def fake_time(clock, monkeypatch):
    """The client's sleeps and clock become the fake clock: retries and backoff take no real time."""
    monkeypatch.setattr(riot_client, "time", SimpleNamespace(sleep=clock.sleep, monotonic=clock))


def make_client(env, clock, responses):
    client = RiotClient(REGIONS["na"])
    client.session = FakeSession(responses)
    return client, client.session


def test_routes_to_platform_and_regional(env, clock):
    client, session = make_client(env, clock, [FakeResponse(body=[]), FakeResponse(body={"info": {}})])
    client.league_entries("GOLD", "II", 3)
    client.match("NA1_1")
    assert session.calls[0]["url"].startswith("https://na1.api.riotgames.com/lol/league/v4/entries/")
    assert session.calls[0]["params"] == {"page": 3}
    assert session.calls[1]["url"] == "https://americas.api.riotgames.com/lol/match/v5/matches/NA1_1"


def test_429_honors_retry_after(env, clock):
    client, session = make_client(env, clock, [
        FakeResponse(429, headers={"Retry-After": "5", "X-Rate-Limit-Type": "application"}),
        FakeResponse(body={"ok": True}),
    ])
    start = clock.t
    assert json.loads(client.match("NA1_1")) == {"ok": True}
    assert clock.t - start >= 5
    assert len(session.calls) == 2


def test_sends_the_key_from_env(env, clock):
    client, session = make_client(env, clock, [FakeResponse(body=[])])
    client.match_ids("puuid", 0, 20)
    assert session.calls[0]["key"] == "RGAPI-test"


def test_rejected_key_raises(env, clock):
    client, session = make_client(env, clock, [FakeResponse(403)])
    with pytest.raises(ApiKeyRejected):
        client.match_ids("puuid", 0, 20)
    assert len(session.calls) == 1  # no retries: a bad key won't get better


def test_missing_key_raises(env, clock):
    env.write_text("")
    with pytest.raises(ApiKeyRejected):
        make_client(env, clock, [])


def test_404_and_400_raise_specific_errors(env, clock):
    client, _ = make_client(env, clock, [FakeResponse(404), FakeResponse(400, body="Exception decrypting")])
    with pytest.raises(NotFound):
        client.match("NA1_1")
    with pytest.raises(BadRequest):
        client.match_ids("bad", 0, 20)


def test_5xx_retries_then_succeeds(env, clock):
    client, _ = make_client(env, clock, [FakeResponse(503), FakeResponse(502), FakeResponse(body={"ok": 1})])
    assert json.loads(client.timeline("NA1_1")) == {"ok": 1}


def test_5xx_gives_up_after_max_failures(env, clock):
    client, session = make_client(env, clock, [FakeResponse(500)] * 5)
    with pytest.raises(RiotApiError):
        client.timeline("NA1_1")
    assert len(session.calls) == 5


def test_rate_limits_reconfigured_from_headers(env, clock):
    headers = {"X-App-Rate-Limit": "500:10,3000:600", "X-App-Rate-Limit-Count": "1:10,1:600"}
    client, _ = make_client(env, clock, [FakeResponse(body={}, headers=headers)])
    client.match("NA1_1")
    assert client._app_limiters["americas"].limits() == [(500, 10.0), (3000, 600.0)]
    assert client._app_limiters["na1"].limits() == [(1_200_000, 600.0)]  # other route untouched


def test_network_outage_waits_instead_of_failing(env, clock):
    # Far more consecutive failures than max_failures: e.g. the laptop slept for an hour.
    outage = [requests.ConnectionError("Failed to resolve host")] * 20
    client, session = make_client(env, clock, [*outage, FakeResponse(body={"ok": 1})])
    assert json.loads(client.match("NA1_1")) == {"ok": 1}
    assert len(session.calls) == 21
    assert max(clock.slept) <= 60  # backoff is capped, so recovery is noticed within a minute


def test_first_request_goes_alone_until_riot_states_the_limits(env):
    class SlowSession:
        def __init__(self):
            self.lock, self.in_flight, self.seen = threading.Lock(), 0, []

        def get(self, url, params=None, headers=None, timeout=None):
            with self.lock:
                self.in_flight += 1
                self.seen.append(self.in_flight)
                first = len(self.seen) == 1
            time.sleep(0.2 if first else 0.01)
            with self.lock:
                self.in_flight -= 1
            return FakeResponse(body={}, headers={"X-Method-Rate-Limit": "2000:10", "X-App-Rate-Limit": "20:1"})

    session = SlowSession()
    client = RiotClient(REGIONS["na"])
    client.session = session
    threads = [threading.Thread(target=client.match, args=("NA1_1",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert session.seen[0] == 1 and session.seen[1] == 1  # nobody joined the first request
    assert len(session.seen) == 8
