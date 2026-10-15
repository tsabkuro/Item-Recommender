import json
import threading
import time

from crawler import storage
from crawler.run import Crawler, reject_reason
from crawler.state import CrawlState


def info(**overrides):
    base = {"queueId": 420, "gameVersion": "16.19.823.722", "gameDuration": 1800,
            "participants": [{"gameEndedInEarlySurrender": False}] * 10}
    base.update(overrides)
    return base


def test_reject_reason():
    assert reject_reason(info(), (16, 19)) is None
    assert reject_reason(info(gameVersion="16.20.1.1"), (16, 19)) is None
    assert reject_reason(info(gameVersion="16.18.1.1"), (16, 19)) == "old_patch"
    assert reject_reason(info(queueId=400), (16, 19)) == "wrong_queue"
    assert reject_reason(info(gameDuration=200), (16, 19)) == "too_short"
    assert reject_reason(info(participants=[{"gameEndedInEarlySurrender": True}]), (16, 19)) == "remake"


OLD_GAME_START = int(time.time()) - 5 * 86400  # every old-patch game in the fake world starts here


class FakeClient:
    """Serves a tiny fake world: one league page, each player has two matches."""

    def __init__(self):
        self.calls = []
        self.start_times = []
        self.matches_fetched = []
        self.pages_fetched = []

    def league_entries(self, tier, division, page):
        self.calls.append("league")
        self.pages_fetched.append((tier, division, page))
        if page > 1:
            return []
        return [{"puuid": f"{tier}-{division}-p{i}", "inactive": False} for i in range(2)]

    def match_ids(self, puuid, start_time, count):
        self.calls.append("ids")
        self.start_times.append(start_time)
        return [f"NA1_{puuid}_new", f"NA1_{puuid}_old"]

    def match(self, match_id):
        self.calls.append("match")
        self.matches_fetched.append(match_id)
        old = match_id.endswith("_old")
        game = info(gameVersion="16.18.1.1" if old else "16.19.1.1",
                    gameStartTimestamp=(OLD_GAME_START if old else int(time.time()) - 3600) * 1000)
        return json.dumps({"metadata": {"matchId": match_id}, "info": game}).encode()

    def timeline(self, match_id):
        self.calls.append("timeline")
        return b'{"info":{"frames":[]}}'


def test_crawler_end_to_end(tmp_path):
    state = CrawlState(tmp_path / "crawl.db")
    client = FakeClient()
    crawler = Crawler("na", client, state, tmp_path, min_patch=(16, 19))
    for _ in range(200):
        crawler.step()

    assert crawler.accepted > 0 and crawler.rejected > 0
    assert client.calls.count("timeline") == crawler.accepted
    done = [state._scalar("SELECT COUNT(*) FROM matches WHERE seed_tier=? AND status='done'", (t,))
            for t in ("SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND")]
    assert max(done) - min(done) <= 1
    files = list((tmp_path / "raw" / "na" / "16.19" / "timelines").glob("*.json.gz"))
    assert len(files) == crawler.accepted
    assert storage.read_json_gz(files[0]) == {"info": {"frames": []}}


def test_workers_never_fetch_the_same_match_twice(tmp_path):
    state = CrawlState(tmp_path / "crawl.db")
    client = FakeClient()
    crawler = Crawler("na", client, state, tmp_path, min_patch=(16, 19))

    def work():
        while crawler.step():
            pass

    workers = [threading.Thread(target=work) for _ in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert len(client.matches_fetched) == len(set(client.matches_fetched)) == 80
    assert crawler.accepted == crawler.rejected == 40
    assert len(client.pages_fetched) == len(set(client.pages_fetched))


def test_matchlist_starts_two_weeks_back(tmp_path):
    client = FakeClient()
    crawler = Crawler("na", client, CrawlState(tmp_path / "crawl.db"), tmp_path, (16, 19))
    crawler.step()
    crawler.step()
    assert client.start_times and abs(client.start_times[0] - (time.time() - 14 * 86400)) < 60


def test_old_patch_game_moves_the_matchlist_start_to_it(tmp_path):
    client = FakeClient()
    state = CrawlState(tmp_path / "crawl.db")
    crawler = Crawler("na", client, state, tmp_path, (16, 19))
    state.add_matches("na", "GOLD", "I", ["NA1_a_old", "NA1_b_new"])
    crawler._process_match("NA1_a_old")
    assert crawler.since == OLD_GAME_START
    crawler._process_match("NA1_b_new")  # accepted games never move it
    assert crawler.since == OLD_GAME_START
    state.add_players("na", "GOLD", "I", ["p1"])
    crawler._expand_player("GOLD")
    assert client.start_times == [OLD_GAME_START]


def test_rejected_key_stops_the_crawl_instead_of_failing_matches(tmp_path):
    import pytest
    from crawler.riot_client import ApiKeyRejected

    class RejectedKey(FakeClient):
        def match(self, match_id):
            raise ApiKeyRejected("HTTP 403")

    state = CrawlState(tmp_path / "crawl.db")
    state.add_matches("na", "GOLD", "I", ["NA1_1"])
    crawler = Crawler("na", RejectedKey(), state, tmp_path, (16, 19))
    with pytest.raises(ApiKeyRejected):
        crawler._process_match("NA1_1")
    assert state._scalar("SELECT attempts FROM matches WHERE match_id='NA1_1'") == 0


def test_player_with_server_error_is_skipped(tmp_path):
    from crawler.riot_client import RiotApiError

    class FailingIds(FakeClient):
        def match_ids(self, puuid, start_time, count):
            raise RiotApiError("HTTP 503")

    state = CrawlState(tmp_path / "crawl.db")
    crawler = Crawler("na", FailingIds(), state, tmp_path, (16, 19))
    for _ in range(10):
        assert crawler.step()  # never raises
    stale = state._scalar("SELECT COUNT(*) FROM players WHERE status='stale'")
    assert stale > 0
