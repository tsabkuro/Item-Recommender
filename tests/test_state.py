import pytest

from crawler.state import CrawlState


@pytest.fixture
def state(tmp_path):
    return CrawlState(tmp_path / "crawl.db")


def test_matches_deduplicated(state):
    assert state.add_matches("na", "GOLD", "II", ["NA1_1", "NA1_2"]) == 2
    assert state.add_matches("na", "SILVER", "I", ["NA1_2", "NA1_3"]) == 1
    assert state.queued_match_count("na", "GOLD") == 2  # NA1_2 keeps its first seed tier
    assert state.queued_match_count("na", "SILVER") == 1


def test_players_deduplicated_per_region(state):
    assert state.add_players("na", "GOLD", "I", ["p1", "p2"]) == 2
    assert state.add_players("na", "GOLD", "I", ["p2"]) == 0
    assert state.add_players("euw", "GOLD", "I", ["p2"]) == 1


def test_tiers_by_progress_prefers_least_done(state):
    state.add_matches("na", "SILVER", "I", ["NA1_1", "NA1_2"])
    state.add_matches("na", "GOLD", "I", ["NA1_3"])
    for m in ("NA1_1", "NA1_2", "NA1_3"):
        state.mark_match_done(m, "16.19")
    order = state.tiers_by_progress("na")
    assert order[-1] == "SILVER"
    assert order[-2] == "GOLD"


def test_failure_marks_failed_after_max_attempts(state):
    state.add_matches("na", "GOLD", "I", ["NA1_1"])
    for _ in range(2):
        assert state.claim_match("na", "GOLD") == "NA1_1"
        assert state.record_match_failure("NA1_1", 3) == "queued"  # back in the queue for a retry
    assert state.claim_match("na", "GOLD") == "NA1_1"
    assert state.record_match_failure("NA1_1", 3) == "failed"
    assert state.claim_match("na", "GOLD") is None


def test_seed_cursor_rotates_divisions_and_exhausts(state):
    claims = [state.claim_seed_page("na", "GOLD") for _ in range(5)]
    assert {d for d, _ in claims[:4]} == {"I", "II", "III", "IV"}
    assert all(page == 1 for _, page in claims[:4])
    assert claims[4] == ("I", 2)  # each claim takes a new page, so no two workers share one
    for d in ("I", "II", "III", "IV"):
        state.mark_seed_exhausted("na", "GOLD", d)
    assert state.claim_seed_page("na", "GOLD") is None


def test_stale_players_leave_queue(state):
    state.add_players("na", "GOLD", "I", ["p1", "p2"])
    state.add_players("euw", "GOLD", "I", ["p3"])
    assert state.mark_region_players_stale("na") == 2
    assert state.claim_player("na", "GOLD") is None
    assert state.claim_player("euw", "GOLD") == ("p3", "I")


def test_claims_hand_out_each_match_once_and_release_on_restart(state):
    state.add_matches("na", "GOLD", "I", ["NA1_1", "NA1_2"])
    state.add_players("na", "GOLD", "I", ["p1"])
    assert {state.claim_match("na", "GOLD"), state.claim_match("na", "GOLD")} == {"NA1_1", "NA1_2"}
    assert state.claim_match("na", "GOLD") is None
    assert state.claim_player("na", "GOLD") == ("p1", "I")
    assert state.claim_player("na", "GOLD") is None
    state.mark_match_done("NA1_1", "16.20")
    # A run stopped mid-request: its unfinished claims go back to the queue.
    assert state.release_claims("na") == 2
    assert state.claim_match("na", "GOLD") == "NA1_2"
    assert state.claim_player("na", "GOLD") == ("p1", "I")
