"""Compare live features with training features for the SAME real game, minute by minute

    python scripts/parity_check.py data/live/<recording>.jsonl.gz [--region na]
"""
import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler import config  # noqa: E402
from crawler.riot_client import RiotClient  # noqa: E402
from live.adapter import to_game_state  # noqa: E402
from live.recorder import read_recording  # noqa: E402
from pipeline.gold import replay_states  # noqa: E402
from pipeline.silver import parse_game  # noqa: E402
from recommender.catalog import Catalog  # noqa: E402
from recommender.features import build_features  # noqa: E402
from recommender.state import GameState  # noqa: E402

TIER = "GOLD"  # same value on both sides; tier isn't part of what's being compared


def find_match(client: RiotClient, puuid: str, game_start_epoch: float, cache: Path) -> tuple[dict, dict]:
    if cache.exists():
        data = json.loads(gzip.decompress(cache.read_bytes()))
        return data["match"], data["timeline"]
    for match_id in client.match_ids(puuid, int(game_start_epoch) - 6 * 3600, 20, ranked_solo_only=False):
        match = json.loads(client.match(match_id))
        if abs(match["info"]["gameStartTimestamp"] / 1000 - game_start_epoch) < 15 * 60:
            timeline = json.loads(client.timeline(match_id))
            cache.write_bytes(gzip.compress(json.dumps({"match": match, "timeline": timeline}).encode()))
            return match, timeline
    raise SystemExit("recorded game not found in Match-V5 (wrong --region, or not processed by Riot yet?)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--region", default="na", choices=sorted(config.REGIONS))
    args = parser.parse_args()

    catalog = Catalog.load(config.STATIC_DIR)
    snaps = read_recording(args.recording)
    me = snaps[0]["data"]["activePlayer"]
    game_start = snaps[0]["wall_time"] - snaps[0]["data"]["gameData"]["gameTime"]

    client = RiotClient(config.REGIONS[args.region])
    cache = args.recording.with_name(args.recording.name.replace(".jsonl.gz", ".match.json.gz"))
    if cache.exists():
        match, timeline = find_match(client, "", game_start, cache)
        puuid = next(p["puuid"] for p in match["info"]["participants"]
                     if p["riotIdGameName"] == me["riotIdGameName"] and p["riotIdTagline"] == me["riotIdTagLine"])
    else:
        puuid = client.account_puuid(me["riotIdGameName"], me["riotIdTagLine"])
        match, timeline = find_match(client, puuid, game_start, cache)
    info = match["info"]
    print(f"Found {match['metadata']['matchId']}: queue {info['queueId']}, {info['gameDuration'] // 60} min, "
          f"patch {info['gameVersion']}")

    meta = {"match_id": match["metadata"]["matchId"], "region": args.region, "patch": "-", "seed_tier": TIER}
    silver = parse_game(match, timeline, meta, catalog)
    my_pid = next(p["participantId"] for p in info["participants"] if p["puuid"] == puuid)
    order = [p["participant_id"] for p in sorted(silver["participants"], key=lambda p: p["participant_id"])]
    me_offline = order.index(my_pid)
    mine = next(p for p in silver["participants"] if p["participant_id"] == my_pid)
    print(f"You: {mine['champion']} {mine['role']} | purchase replay matches final items: {mine['replay_ok']}")

    # Live kill times vs timeline kill times: are the two clocks aligned?
    live_kills = sorted(e["EventTime"] for s in snaps[-1:] for e in s["data"]["events"]["Events"]
                        if e["EventName"] == "ChampionKill")
    tl_kills = sorted(k["timestamp_ms"] / 1000 for k in silver["kills"])
    offsets = [a - b for a, b in zip(live_kills, tl_kills)]
    if offsets:
        print(f"Clock offset live - timeline over {len(offsets)} kills: median {sorted(offsets)[len(offsets) // 2]:+.2f}s")

    times = [s["data"]["gameData"]["gameTime"] for s in snaps]
    diffs, minutes = Counter(), 0
    examples = {}
    for t_ms, players, frame, *_ in replay_states(silver["participants"], silver["item_events"], silver["kills"],
                                              silver["frames"], catalog):
        t = t_ms / 1000
        if t < 60 or t > times[-1]:
            continue
        nearest = min(range(len(snaps)), key=lambda i: abs(times[i] - t))
        if abs(times[nearest] - t) > 1.0:
            continue
        offline = build_features(GameState(t, players, me_offline, frame[my_pid]["current_gold"], TIER), catalog)
        live = build_features(to_game_state(snaps[nearest]["data"], catalog, TIER), catalog)
        minutes += 1
        for key in offline:
            a, b = offline[key], live[key]
            same = abs(a - b) < 1e-6 if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a == b
            if key in ("minute",):
                same = abs(a - b) < 1 / 60  # snapshots are up to 0.5s apart from frame times
            if not same:
                diffs[key] += 1
                examples.setdefault(key, (round(t / 60, 1), a, b))

    n_features = len(offline)
    print(f"\nCompared {minutes} minutes x {n_features} features.")
    if not diffs:
        print("All features identical at every minute.")
    for key, n in diffs.most_common():
        minute, a, b = examples[key]
        print(f"  {key:<24} differs in {n:>2}/{minutes} minutes   e.g. min {minute}: training={a!r} live={b!r}")


if __name__ == "__main__":
    main()
