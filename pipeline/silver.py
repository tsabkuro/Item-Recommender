"""Bronze -> silver: raw match + timeline JSON -> normalized Parquet tables

    python -m pipeline.silver [--limit 1000] [--workers 8]

Tables (data/silver/<table>/part-*.parquet):
    games           one row per game: region, patch, seed tier, duration, winner
    participants    one row per player: champion, team, role, keystone, spells, win, final items, replay_ok
    item_events     purchases / sells / destroys / undos in original order (participant 0 dropped because its not a player)
    kills           champion kills with killer, victim, assists
    frames          per player per minute: gold, level, xp, cs
    quarantine      games rejected by structural checks, with the reason

Every run is a full rebuild into a temp dir that replaces data/silver at the end
    - silver is a pure function of bronze

Data-quality checks:
    - structural (quarantines the game): 10 participants, 5v5 teams, one of each role per
        team, timeline and match agree on who is who, known champions, a real timeline, no
        item-swapping champion (Viego)
    - replay (flag): replaying the item events must reproduce the finished
        items the match says the player ended with
        Players failing this keep their rows but can't be used as training labels
"""
import argparse
import gzip
import os
import random
import shutil
import sqlite3
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import orjson
import polars as pl

from crawler import config as crawler_config
from crawler.storage import raw_path
from recommender.catalog import Catalog
from recommender.inventory import Inventory
from recommender.state import ROLES

TABLES = ("games", "participants", "item_events", "kills", "frames", "quarantine")
ITEM_EVENT_TYPES = {"ITEM_PURCHASED", "ITEM_DESTROYED", "ITEM_SOLD", "ITEM_UNDO"}
BANNED_CHAMPIONS = {"Viego"}
CHUNK_SIZE = 250

_catalog: Catalog | None = None  # one per worker process


class Quarantine(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def parse_game(match: dict, timeline: dict, data: dict, catalog: Catalog) -> dict[str, list[dict]]:
    """One game's raw JSON -> rows for each silver table"""
    info, tinfo = match["info"], timeline["info"]
    match_id = data["match_id"]
    participants = info["participants"]

    if sorted(p["participantId"] for p in participants) != list(range(1, 11)):
        raise Quarantine("participant_count", str(len(participants)))
    if Counter(p["teamId"] for p in participants) != {100: 5, 200: 5}:
        raise Quarantine("team_sizes")
    for team in (100, 200):
        roles = sorted(p["teamPosition"] for p in participants if p["teamId"] == team)
        if roles != sorted(ROLES):
            raise Quarantine("roles", f"team {team}: {roles}")
    if {p["participantId"]: p["puuid"] for p in participants} != {p["participantId"]: p["puuid"] for p in tinfo["participants"]}:
        raise Quarantine("participant_mismatch")
    champions = {}
    for p in participants:
        champ = catalog.champion_by_key(p["championId"])
        if champ is None:
            raise Quarantine("unknown_champion", str(p["championId"]))
        if champ.id in BANNED_CHAMPIONS:
            raise Quarantine("banned_champion", champ.id)
        champions[p["participantId"]] = champ.id
    frames = tinfo["frames"]
    if len(frames) < 5:
        raise Quarantine("short_timeline", str(len(frames)))

    item_events, kills, frame_rows = [], [], []
    inventories = {pid: Inventory(catalog.silent_upgrades) for pid in range(1, 11)}
    for frame in frames:
        for e in frame["events"]:
            kind = e["type"]
            if kind in ITEM_EVENT_TYPES:
                pid = e.get("participantId", 0)
                if not 1 <= pid <= 10:
                    continue  # ignore participant 0
                # seq keeps the original order
                row = {"match_id": match_id, "seq": len(item_events), "participant_id": pid,
                    "timestamp_ms": e["timestamp"], "type": kind,
                    "item_id": e.get("itemId", 0), "before_id": e.get("beforeId", 0),
                    "after_id": e.get("afterId", 0)}
                item_events.append(row)
                inventories[pid].apply(kind, row["timestamp_ms"], row["item_id"], row["before_id"], row["after_id"])
            elif kind == "CHAMPION_KILL":
                kills.append({"match_id": match_id, "timestamp_ms": e["timestamp"], "killer_id": e.get("killerId", 0),
                    "victim_id": e["victimId"], "assist_ids": e.get("assistingParticipantIds", [])})
        for pid in range(1, 11):
            pf = frame["participantFrames"][str(pid)]
            frame_rows.append({"match_id": match_id, "participant_id": pid, "timestamp_ms": frame["timestamp"],
                "current_gold": pf["currentGold"], "total_gold": pf["totalGold"],
                "level": pf["level"], "xp": pf["xp"],
                "cs": pf["minionsKilled"] + pf["jungleMinionsKilled"]})

    winner = next((t["teamId"] for t in info["teams"] if t.get("win")), 0)
    participant_rows = []
    for p in participants:
        pid = p["participantId"]
        # ADCs get an extra boots slot, recorded separately
        final_items = [p[f"item{i}"] for i in range(7) if p[f"item{i}"]]
        if p.get("roleBoundItem"):
            final_items.append(p["roleBoundItem"])
        replay_ok = sorted(catalog.finished_owned(final_items)) == sorted(catalog.finished_owned(inventories[pid].items))
        styles = p.get("perks", {}).get("styles", [])
        keystone = styles[0]["selections"][0]["perk"] if styles and styles[0].get("selections") else 0
        participant_rows.append({"match_id": match_id, "participant_id": pid, "champion": champions[pid],
            "champion_key": p["championId"], "team": p["teamId"], "role": p["teamPosition"],
            "keystone": keystone, "spell1": p.get("summoner1Id", 0),
            "spell2": p.get("summoner2Id", 0),
            "win": p["teamId"] == winner, "final_items": final_items, "replay_ok": replay_ok,
            "replay_anomalies": inventories[pid].anomalies})

    games = [{"match_id": match_id, "region": data["region"], "patch": data["patch"], "seed_tier": data["seed_tier"],
        "game_version": info["gameVersion"], "game_start_ms": info["gameStartTimestamp"],
        "duration_s": info["gameDuration"], "winning_team": winner}]
    return {"games": games, "participants": participant_rows, "item_events": item_events, "kills": kills,
        "frames": frame_rows}


def _init_worker(static_dir: str) -> None:
    """Runs once in each worker process"""
    global _catalog
    _catalog = Catalog.load(Path(static_dir))


def _process_part(args: tuple[int, list[dict], str]) -> dict[str, int]:
    """One chunk of bronze games -> part-<id>.parquet of every silver table"""
    part_id, games, out_dir = args
    rows = {t: [] for t in TABLES}
    for data in games:
        try:
            match = orjson.loads(gzip.decompress(Path(data["match_path"]).read_bytes()))
            timeline = orjson.loads(gzip.decompress(Path(data["timeline_path"]).read_bytes()))
            for table, table_rows in parse_game(match, timeline, data, _catalog).items():
                rows[table].extend(table_rows)
        except Quarantine as q:
            rows["quarantine"].append({"match_id": data["match_id"], "region": data["region"], "patch": data["patch"],
                "reason": q.reason, "detail": q.detail})
        except (OSError, ValueError, KeyError, EOFError) as exc:
            rows["quarantine"].append({"match_id": data["match_id"], "region": data["region"], "patch": data["patch"],
                "reason": "unreadable", "detail": f"{type(exc).__name__}: {exc}"[:200]})
    for table, table_rows in rows.items():
        if table_rows:
            path = Path(out_dir) / table / f"part-{part_id:05d}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(table_rows).write_parquet(path)
    return {t: len(r) for t, r in rows.items()}


def load_bronze(data_dir: Path, limit: int | None, seed: int = 0) -> list[dict]:
    """Downloaded games from the crawler's database (bronze)"""
    db = data_dir / crawler_config.DB_FILENAME
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT match_id, region, patch, seed_tier FROM matches WHERE status='done' ORDER BY match_id"
    ).fetchall()
    if limit:
        rows = random.Random(seed).sample(rows, min(limit, len(rows)))
    return [{"match_id": m, "region": r, "patch": p, "seed_tier": t,
             "match_path": str(raw_path(data_dir, r, p, "matches", m)),
             "timeline_path": str(raw_path(data_dir, r, p, "timelines", m))} for m, r, p, t in rows]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="random sample of N games (default: all)")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args(argv)

    started = time.monotonic()
    final_dir = crawler_config.DATA_DIR / "silver"
    tmp_dir = crawler_config.DATA_DIR / "silver.tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    bronze = load_bronze(crawler_config.DATA_DIR, args.limit)
    tasks = [(i, bronze[s:s + CHUNK_SIZE], str(tmp_dir)) for i, s in enumerate(range(0, len(bronze), CHUNK_SIZE))]
    print(f"Processing {len(bronze)} games in {len(tasks)} parts with {args.workers} workers...")

    totals = Counter()
    with ProcessPoolExecutor(args.workers, initializer=_init_worker, initargs=(str(crawler_config.STATIC_DIR),)) as pool:
        for counts in pool.map(_process_part, tasks):
            totals.update(counts)

    shutil.rmtree(final_dir, ignore_errors=True)
    tmp_dir.rename(final_dir)
    print(f"Done in {time.monotonic() - started:.0f}s -> {final_dir}")
    for table, n in totals.items():
        print(f"  {table:<13} {n:>10,} rows")
    report(final_dir)


def report(silver_dir: Path) -> None:
    games = pl.scan_parquet(silver_dir / "games" / "*.parquet").select(pl.len()).collect().item()
    quarantine_files = list((silver_dir / "quarantine").glob("*.parquet"))
    if quarantine_files:
        q = pl.read_parquet(quarantine_files)
        print(f"\nQuarantined {len(q)} of {games + len(q)} games ({len(q) / (games + len(q)):.2%}):")
        print(q.group_by("reason").len().sort("len", descending=True))
    participants = pl.scan_parquet(silver_dir / "participants" / "*.parquet")
    ok = participants.select(pl.col("replay_ok").mean()).collect().item()
    print(f"\nPurchase replay reproduces final finished items for {ok:.2%} of players")
    print(participants.group_by("role").agg(pl.col("replay_ok").mean().round(4)).sort("role").collect())


if __name__ == "__main__":
    main()
