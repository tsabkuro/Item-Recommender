"""Silver -> gold: one training example per (player, sample time)

    python -m pipeline.gold [--workers 8]

For each game, at sampled minutes:
    - rebuild the 10-player GameState from silver (items via event replay, gold/level/CS
        from that minute's frame, kills so far)
    - compute features with `build_features`
    - label each row with the next finished item that player completes
Writes data/gold/examples/part-*.parquet

Sampling
    - 1.5 minutes apart on average, every feature taken from the same moment

Enemy items are lagged, since the live API shows enemy items as last seen (fog of war)
    - each enemy's inventory comes from a random earlier frame, with the staleness measured
        on a recorded game (ENEMY_ITEM_LAG). allies stay current

Labels:
    - only players whose replay matched their final items (replay_ok) are the "me" of a row
"""
import argparse
import os
import random
import shutil
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import polars as pl

from crawler import config as crawler_config
from recommender.catalog import Catalog
from recommender.features import build_features
from recommender.inventory import Inventory
from recommender.state import GameState, PlayerState

SAMPLE_EVERY_MIN = 1.5
# How stale live enemy items are, minutes -> probability
ENEMY_ITEM_LAG = {0: 0.75, 1: 0.22, 2: 0.02, 3: 0.01}
NO_LAG = {0: 1.0}
SILVER_TABLES = ("games", "participants", "item_events", "kills", "frames")

_catalog: Catalog | None = None  # one per worker process


def sample_frames(n_frames: int, every: float = SAMPLE_EVERY_MIN) -> list[int]:
    """Frame indices `x` minutes apart, skipping frame 0 and the last"""
    picks, j = [], 1
    while (k := int(j * every)) < n_frames - 1:
        if k >= 1 and (not picks or k != picks[-1]):
            picks.append(k)
        j += 1
    return picks


def completions(events: list[dict], catalog: Catalog) -> list[tuple[int, int]]:
    """(timestamp, finished item) for each finished item the player bought and kept (not undone)"""
    inv = Inventory(catalog.silent_upgrades)
    for e in events:
        inv.apply(e["type"], e["timestamp_ms"], e["item_id"], e["before_id"], e["after_id"])
    return [(ts, catalog.canonical(item)) for ts, item in inv.purchases if catalog.is_finished(item)]


def replay_states(participants: list[dict], item_events: list[dict], kills: list[dict], frames: list[dict],
        catalog: Catalog, frame_indices: list[int] | None = None):
    """
    Yield (timestamp_ms, players, frame rows by participant, frame index, item history) at the chosen frames
        - players: PlayerStates in participant order, rebuilt from the events up to that moment
        - item_history[participant_id][i]: that player's items at frame i (for lagging enemy items)
        - frame_indices=None means every frame
    """
    parts = sorted(participants, key=lambda p: p["participant_id"])
    events = sorted(item_events, key=lambda e: e["seq"])
    frame_at = defaultdict(dict)  # frame timestamp -> participant -> frame row
    for f in frames:
        frame_at[f["timestamp_ms"]][f["participant_id"]] = f
    frame_times = sorted(frame_at)
    kills = sorted(kills, key=lambda k: k["timestamp_ms"])

    inventories = {p["participant_id"]: Inventory(catalog.silent_upgrades) for p in parts}
    item_history = {p["participant_id"]: [] for p in parts}
    wanted = set(range(len(frame_times)) if frame_indices is None else frame_indices)
    k_count, d_count, a_count = Counter(), Counter(), Counter()
    ei = ki = 0
    for fi in range(max(wanted, default=-1) + 1):
        t = frame_times[fi]
        while ei < len(events) and events[ei]["timestamp_ms"] <= t:
            e = events[ei]
            inventories[e["participant_id"]].apply(e["type"], e["timestamp_ms"], e["item_id"], e["before_id"], e["after_id"])
            ei += 1
        while ki < len(kills) and kills[ki]["timestamp_ms"] <= t:
            kill = kills[ki]
            k_count[kill["killer_id"]] += 1
            d_count[kill["victim_id"]] += 1
            for assist in kill["assist_ids"]:
                a_count[assist] += 1
            ki += 1
        for pid, inv in inventories.items():
            item_history[pid].append(tuple(inv.items))
        if fi not in wanted:
            continue
        players = tuple(
            PlayerState(champion=p["champion"], team=p["team"], role=p["role"],
                items=tuple(inventories[p["participant_id"]].items),
                level=frame_at[t][p["participant_id"]]["level"], kills=k_count[p["participant_id"]],
                deaths=d_count[p["participant_id"]], assists=a_count[p["participant_id"]],
                cs=frame_at[t][p["participant_id"]]["cs"], keystone=p["keystone"],
                spells=(p["spell1"], p["spell2"]))
            for p in parts)
        yield t, players, frame_at[t], fi, item_history


def build_game_examples(game: dict, participants: list[dict], item_events: list[dict], kills: list[dict],
        frames: list[dict], catalog: Catalog, every: float = SAMPLE_EVERY_MIN,
        enemy_lag: dict[int, float] = ENEMY_ITEM_LAG) -> list[dict]:
    parts = sorted(participants, key=lambda p: p["participant_id"])
    by_player = defaultdict(list)
    for e in sorted(item_events, key=lambda e: e["seq"]):
        by_player[e["participant_id"]].append(e)
    done = {p["participant_id"]: completions(by_player[p["participant_id"]], catalog) for p in parts}
    done_times = {pid: [ts for ts, _ in d] for pid, d in done.items()}
    n_frames = len({f["timestamp_ms"] for f in frames})
    rng = random.Random(game["match_id"])  # deterministic, so rebuilding gold gives identical rows
    lags, lag_weights = list(enemy_lag), list(enemy_lag.values())

    rows = []
    frame_indices = sample_frames(n_frames, every)
    for t, players, frame, fi, history in replay_states(parts, item_events, kills, frames, catalog, frame_indices):
        for me, p in enumerate(parts):
            pid = p["participant_id"]
            if not p["replay_ok"]:
                continue
            nxt = bisect_right(done_times[pid], t)
            if nxt == len(done[pid]):
                continue  # no more finished items this game
            label_time, label = done[pid][nxt]
            view = tuple(
                q if q.team == p["team"]
                else replace(q, items=history[parts[j]["participant_id"]][max(0, fi - rng.choices(lags, lag_weights)[0])])
                for j, q in enumerate(players))
            state = GameState(game_time=t / 1000, players=view, me=me, my_gold=frame[pid]["current_gold"],
                tier=game["seed_tier"])
            rows.append({"match_id": game["match_id"], "participant_id": pid, "region": game["region"],
                "patch": game["patch"], "game_start_ms": game["game_start_ms"], "win": p["win"],
                **build_features(state, catalog), "label": label, "seconds_to_label": (label_time - t) / 1000})
    return rows


def _init_worker(static_dir: str) -> None:
    """Runs once in each worker process"""
    global _catalog
    _catalog = Catalog.load(Path(static_dir))


def _process_part(args: tuple[str, str, str]) -> dict[str, int]:
    """One silver part (every table's part-<id>.parquet) -> part-<id>.parquet of training examples"""
    part_name, silver_dir, out_dir = args
    tables = {}
    for t in SILVER_TABLES:
        path = Path(silver_dir) / t / part_name
        tables[t] = pl.read_parquet(path) if path.exists() else None
    if tables["games"] is None:
        return {"examples": 0}  # chunk was entirely quarantined
    grouped = {t: {k[0]: g.to_dicts() for k, g in tables[t].group_by("match_id")}
        for t in ("participants", "item_events", "kills", "frames")}
    rows = []
    for game in tables["games"].to_dicts():
        mid = game["match_id"]
        rows.extend(build_game_examples(game, grouped["participants"][mid], grouped["item_events"].get(mid, []),
            grouped["kills"].get(mid, []), grouped["frames"][mid], _catalog))
    if rows:
        path = Path(out_dir) / part_name
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_parquet(path)
    return {"examples": len(rows)}


def load_silver(silver_dir: Path) -> list[str]:
    """Silver part files to process, one per chunk of bronze games"""
    return sorted(p.name for p in (silver_dir / "games").glob("part-*.parquet"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args(argv)

    started = time.monotonic()
    final_dir = crawler_config.DATA_DIR / "gold" / "examples"
    tmp_dir = crawler_config.DATA_DIR / "gold" / "examples.tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    silver_dir = crawler_config.DATA_DIR / "silver"
    parts = load_silver(silver_dir)
    tasks = [(part, str(silver_dir), str(tmp_dir)) for part in parts]
    print(f"Processing {len(parts)} silver parts with {args.workers} workers...")

    totals = Counter()
    with ProcessPoolExecutor(args.workers, initializer=_init_worker, initargs=(str(crawler_config.STATIC_DIR),)) as pool:
        for counts in pool.map(_process_part, tasks):
            totals.update(counts)

    shutil.rmtree(final_dir, ignore_errors=True)
    tmp_dir.rename(final_dir)
    print(f"Done in {time.monotonic() - started:.0f}s -> {final_dir}")
    for table, n in totals.items():
        print(f"  {table:<13} {n:>10,} rows")
    report(final_dir, Catalog.load(crawler_config.STATIC_DIR))


def report(examples_dir: Path, catalog: Catalog) -> None:
    ex = pl.scan_parquet(examples_dir / "*.parquet")
    labels = ex.group_by("label").len().sort("len", descending=True).collect()
    n = labels["len"].sum()
    print(f"\n{n:,} examples, {len(labels)} distinct labels ({(labels['len'] >= 500).sum()} with >= 500 examples)")
    print("Top labels:", ", ".join(f"{catalog.items[i].name if i in catalog.items else i} {c / n:.1%}"
        for i, c in labels.head(8).iter_rows()))
    by_role = (ex.group_by("role", "label").len().sort("len", descending=True)
        .group_by("role", maintain_order=True).head(3).sort("role").collect())
    print("\nMost common labels per role:")
    for role, group in by_role.group_by("role", maintain_order=True):
        names = [f"{catalog.items[i].name if i in catalog.items else i} ({c:,})"
            for i, c in zip(group["label"], group["len"])]
        print(f"  {role[0]:<8} {', '.join(names)}")


if __name__ == "__main__":
    main()
