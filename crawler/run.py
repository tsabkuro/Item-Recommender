"""Crawl ranked Match-V5 matches + timelines per region

Usage:
    python -m crawler.run --region na [--max-matches 20]
But instead, use scripts/start_crawlers.sh, which syncs Data Dragon first and starts one process per region

Keeps games from the current patch (the one in the local Data Dragon copy)
The processes share data/crawl.db
Each process runs WORKERS_PER_REGION threads that share one rate-limited client
"""
import argparse
import json
import logging
import shutil
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from crawler import config, ddragon, storage
from crawler.riot_client import ApiKeyRejected, BadRequest, NotFound, RiotApiError, RiotClient
from crawler.state import CrawlState

log = logging.getLogger("crawler")


def reject_reason(info: dict, min_patch: tuple[int, int]) -> str | None:
    if info.get("queueId") != config.QUEUE_ID:
        return "wrong_queue"
    patch = ddragon.patch_of(info.get("gameVersion", ""))
    if patch is None or patch < min_patch:
        return "old_patch"
    if info.get("gameDuration", 0) < config.MIN_GAME_DURATION_S:
        return "too_short"
    if any(p.get("gameEndedInEarlySurrender") for p in info.get("participants", [])):
        return "remake"
    return None


class Crawler:
    def __init__(self, region_name: str, client: RiotClient, state: CrawlState, data_dir: Path,
                min_patch: tuple[int, int]):
        self.region = region_name
        self.client = client
        self.state = state
        self.data_dir = data_dir
        self.min_patch = min_patch
        self.since = int(time.time()) - config.MATCHLIST_LOOKBACK_DAYS * 86400 # only list games started after this
        self.accepted = 0
        self.rejected = 0
        self._lock = threading.Lock()

    def _count(self, accepted: bool) -> None:
        with self._lock:
            if accepted:
                self.accepted += 1
            else:
                self.rejected += 1

    def step(self) -> bool:
        """Do one unit of work for the least-complete tier that has any"""
        return any(self._work_on(tier) for tier in self.state.tiers_by_progress(self.region))

    def _work_on(self, tier: str) -> bool:
        # Lazy discovery. only pull more match IDs when this tiers queue is small
        if self.state.queued_match_count(self.region, tier) < config.MIN_QUEUED_MATCHES_PER_TIER:
            if self._expand_player(tier) or self._seed(tier):
                return True
        match_id = self.state.claim_match(self.region, tier)
        if match_id is None:
            return False
        self._process_match(match_id)
        return True

    def _seed(self, tier: str) -> bool:
        cursor = self.state.claim_seed_page(self.region, tier)
        if cursor is None:
            return False
        division, page = cursor
        entries = self.client.league_entries(tier, division, page)
        if not entries:
            self.state.mark_seed_exhausted(self.region, tier, division)
        puuids = [e["puuid"] for e in entries if e.get("puuid") and not e.get("inactive")]
        added = self.state.add_players(self.region, tier, division, puuids)
        log.info("seed %s %s page %d: %d new players", tier, division, page, added)
        return True

    def _expand_player(self, tier: str) -> bool:
        player = self.state.claim_player(self.region, tier)
        if player is None:
            return False
        puuid, division = player
        # ask only for games after the patch went live, so old-patch games cost zero calls
        try:
            ids = self.client.match_ids(puuid, self.since, config.MATCHES_PER_PLAYER)
        except RiotApiError as exc:
            if isinstance(exc, BadRequest) and "decrypt" in str(exc).lower():
                dropped = self.state.mark_region_players_stale(self.region)
                log.warning("PUUIDs invalid for current key; dropped %d queued players, re-seeding", dropped)
            else:
                # skip bad player
                log.warning("skipping player: %s", exc)
                self.state.mark_player(self.region, puuid, "stale")
            return True
        added = self.state.add_matches(self.region, tier, division, ids)
        self.state.mark_player(self.region, puuid, "done")
        log.debug("expand %s player: %d ids, %d new", tier, len(ids), added)
        return True

    def _process_match(self, match_id: str) -> None:
        try:
            match = self.client.match(match_id)
            info = json.loads(match)["info"]
            reason = reject_reason(info, self.min_patch)
            patch = ddragon.patch_of(info.get("gameVersion", ""))
            patch_s = f"{patch[0]}.{patch[1]}" if patch else None
            if reason:
                if reason == "old_patch":
                    self._skip_games_before(info.get("gameStartTimestamp", 0) // 1000) # riot gives in ms so convert to s
                self.state.mark_match_rejected(match_id, reason, patch_s)
                self._count(accepted=False)
                return
            timeline = self.client.timeline(match_id)
        except (NotFound, BadRequest) as exc:
            self.state.mark_match_rejected(match_id, type(exc).__name__)
            self._count(accepted=False)
            return
        except RiotApiError as exc:
            status = self.state.record_match_failure(match_id, config.MAX_MATCH_ATTEMPTS)
            log.warning("match %s failed (%s), now %s", match_id, exc, status)
            return

        storage.write_gz(storage.raw_path(self.data_dir, self.region, patch_s, "matches", match_id), match)
        storage.write_gz(storage.raw_path(self.data_dir, self.region, patch_s, "timelines", match_id), timeline)
        self.state.mark_match_done(match_id, patch_s)
        self._count(accepted=True)

    def _skip_games_before(self, epoch_s: int) -> None:
        with self._lock:
            if epoch_s > self.since:
                self.since = epoch_s
                log.info("old-patch game found: now listing games from %s", datetime.fromtimestamp(epoch_s, timezone.utc).isoformat())


def setup_logging(region: str) -> None:
    log_dir = config.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(f"%(asctime)s [{region}] %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_dir / f"{region}.log")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def free_disk_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", required=True, choices=sorted(config.REGIONS))
    parser.add_argument("--max-matches", type=int, default=0, help="stop once N matches are accepted (0 = run forever)")
    args = parser.parse_args(argv)
    data_dir = config.DATA_DIR

    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(args.region)

    version = ddragon.local_version()
    if version is None:
        parser.error("no local Data Dragon copy: run `python -m crawler.ddragon` first")
    min_patch = ddragon.patch_of(version)

    state = CrawlState(data_dir / config.DB_FILENAME)
    released = state.release_claims(args.region) # whatever was claimed but unfinished from last run is released
    client = RiotClient(config.REGIONS[args.region])
    crawler = Crawler(args.region, client, state, data_dir, min_patch)
    stop = threading.Event()

    def request_stop(signum):
        log.info("Interrupted (signal %d)", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    def work() -> None:
        while not stop.is_set():
            if args.max_matches and crawler.accepted >= args.max_matches:
                stop.set()
                return
            try:
                did_work = crawler.step()
            except ApiKeyRejected as exc:
                log.error("%s; stopping", exc)
                stop.set()
                return
            except Exception:
                log.exception("Unexpected error; pausing 30s and continuing")
                stop.wait(30)
                continue
            if not did_work:
                log.warning("No work available (all seeds exhausted?), sleeping 60s")
                stop.wait(60)

    log.info("Starting region=%s patch=%d.%d (Data Dragon %s) workers=%d (re-queued %d unfinished claims)",
             args.region, *min_patch, version, config.WORKERS_PER_REGION, released)

    for _ in range(config.WORKERS_PER_REGION):
        threading.Thread(target=work, daemon=True).start()

    started = last_stats = time.monotonic()
    while not stop.wait(5):
        if free_disk_gb(data_dir) < config.MIN_FREE_DISK_GB:
            log.error("Free disk below %d GB, stopping", config.MIN_FREE_DISK_GB)
            break
        now = time.monotonic()
        if now - last_stats >= config.STATS_INTERVAL_S:
            hours = (now - started) / 3600
            log.info("stats: accepted=%d rejected=%d requests=%d 429s=%d (%.0f matches/h, %.1f req/s)",
                     crawler.accepted, crawler.rejected, client.requests_made, client.throttled,
                     crawler.accepted / hours, client.requests_made / (now - started))
            last_stats = now

    log.info("Stopped: accepted=%d rejected=%d requests=%d 429s=%d",
             crawler.accepted, crawler.rejected, client.requests_made, client.throttled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
