"""Record live game snapshots to disk

Start it, then play: it waits for a game, records every snapshot until
the game ends, then waits for the next game. Ctrl+C to stop

Usage:
    python -m live.recorder [--interval 0.5]

Output: data/live/<UTC start time>_<champion>.jsonl.gz
    {"wall_time": <unix seconds>, "data": <allgamedata response>}
"""
import argparse
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from crawler.config import RECORDINGS_DIR
from live.adapter import UnknownPlayer, active_index
from live.client import LiveClient

FLUSH_EVERY = 20


def read_recording(path: Path) -> list[dict]:
    lines = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        try:
            for line in f:
                lines.append(json.loads(line))
        except EOFError:
            pass
    return lines


def my_champion(data: dict) -> str:
    try:
        return data["allPlayers"][active_index(data)].get("championName", "unknown")
    except UnknownPlayer:
        return "unknown"


def game_ended(data: dict) -> bool:
    return any(e.get("EventName") == "GameEnd" for e in data.get("events", {}).get("Events", []))


class Recorder:
    def __init__(self, client: LiveClient, out_dir: Path, interval: float = 0.5, idle_interval: float = 2.0,
            sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print):
        self.client = client
        self.out_dir = out_dir
        self.interval = interval
        self.idle_interval = idle_interval
        self.sleep = sleep
        self.log = log
        self._file = None
        self.path: Path | None = None
        self.count = 0
        self._last_game_time = -1.0
        self._finished = False
        self._last_status = 0.0

    def _open(self, data: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        champion = "".join(ch for ch in my_champion(data) if ch.isalnum()) or "unknown"
        self.path = self.out_dir / f"{stamp}_{champion}.jsonl.gz"
        self._file = gzip.open(self.path, "at", encoding="utf-8")
        self.count = 0
        self.log(f"Recording {champion} -> {self.path}")

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self.log(f"Saved {self.count} snapshots to {self.path}")

    def poll_once(self) -> float:
        """Fetch one snapshot and record it if new"""
        data = self.client.fetch()
        if data is None:
            if self._file:
                self.log("Game client gone.")
                self.close()
            self._finished = False
            self._last_game_time = -1.0
            return self.idle_interval

        game_time = float(data["gameData"].get("gameTime", 0.0))
        if game_time < self._last_game_time - 5:  # clock went backwards: a new game started
            self.close()
            self._finished = False
        if self._finished:
            return self.idle_interval
        if self._file is None:
            self._open(data)

        if game_time != self._last_game_time:  # skip duplicates (paused game, faster polling than updates)
            self._file.write(json.dumps({"wall_time": time.time(), "data": data}, separators=(",", ":")) + "\n")
            self.count += 1
            if self.count % FLUSH_EVERY == 0:
                self._file.flush()
            self._last_game_time = game_time

        if time.monotonic() - self._last_status >= 10:
            gold = data.get("activePlayer", {}).get("currentGold", 0)
            self.log(f"  game time {game_time / 60:5.1f} min | gold {gold:6.0f} | {self.count} snapshots")
            self._last_status = time.monotonic()

        if game_ended(data):
            self.log("Game over.")
            self.close()
            self._finished = True
        return self.interval

    def run(self) -> None:
        self.log("Waiting for a game... (start this before or during a game; Ctrl+C to stop)")
        try:
            while True:
                self.sleep(self.poll_once())
        except KeyboardInterrupt:
            pass
        finally:
            self.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between polls during a game")
    args = parser.parse_args()
    Recorder(LiveClient(), RECORDINGS_DIR, interval=args.interval).run()


if __name__ == "__main__":
    main()
