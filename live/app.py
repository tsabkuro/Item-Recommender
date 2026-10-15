"""App

    python -m live.app                                                  # during a real game
    python -m live.app --replay latest --speed 10 --start-minute 5      # replay the newest recording
    python -m live.app --replay data/live/<file>.jsonl.gz               # replay a specific one

Then open http://localhost:8765 (build the page once first: cd web && npm install && npm run build)

A background thread polls the game (or the recording) every 0.5s and recomputes the
recommendations, while the page polls GET /api/state
During a replay the page can also pause, seek and change speed (POST /api/replay/...)
"""
import argparse
import threading
import time
from bisect import bisect_right
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crawler.config import MODELS_DIR, RECORDINGS_DIR, STATIC_DIR, TIERS
from live.adapter import UnknownPlayer
from live.client import LiveClient
from live.recommend import LiveRecommender
from live.recorder import read_recording
from recommender.catalog import Catalog

POLL_INTERVAL = 0.5
REPLAY_SPEEDS = (1, 5, 10, 30)
WIN_HISTORY_STEP_S = 5  # one chart point per 5 game seconds keeps /api/state small
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def resolve_recording(arg: str) -> Path:
    """'latest' -> newest recording in data/live, otherwise a path"""
    if arg != "latest":
        return Path(arg)
    recordings = sorted(RECORDINGS_DIR.glob("*.jsonl.gz"))
    if not recordings:
        raise SystemExit(f"no recordings in {RECORDINGS_DIR}")
    return recordings[-1]


class ReplaySource:
    """
    Plays a recording back as if it were live, `speed` times faster than real time, starting
    `start_minute` into the game

    The page can pause, seek and change speed (POST /api/replay)
    """

    def __init__(self, path: Path, speed: float, start_minute: float = 0.0):
        self.snapshots = [line["data"] for line in read_recording(path)]
        self.times = [s["gameData"]["gameTime"] for s in self.snapshots]
        self.name = path.name
        self.lock = threading.Lock()
        self.speed = speed
        self.paused = False
        self.anchor_game, self.anchor_wall = 0.0, time.monotonic()
        self.seek(start_minute * 60)

    @property
    def duration(self) -> float:
        return self.times[-1]

    def _position(self) -> float:
        if self.paused:
            return self.anchor_game
        return min(self.anchor_game + (time.monotonic() - self.anchor_wall) * self.speed, self.duration)

    def _set(self, game_time: float, **changes) -> None:
        with self.lock:
            self.anchor_game = max(0.0, min(game_time, self.duration))
            self.anchor_wall = time.monotonic()
            for key, value in changes.items():
                setattr(self, key, value)

    def seek(self, game_time: float) -> None:
        self._set(game_time)

    def set_speed(self, speed: float) -> None:
        with self.lock:
            now = self._position()
        self._set(now, speed=speed)

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            now = self._position()
        self._set(now, paused=paused)

    def status(self) -> dict:
        with self.lock:
            position = self._position()
            return {"name": self.name, "position": position, "duration": self.duration, "speed": self.speed,
                    "paused": self.paused, "ended": position >= self.duration}

    def fetch(self) -> dict | None:
        with self.lock:
            target = self._position()
        return self.snapshots[max(bisect_right(self.times, target) - 1, 0)]


class State:
    def __init__(self, tier: str):
        self.lock = threading.Lock()
        self.tier = tier
        self.latest: dict = {"status": "waiting", "message": "Waiting for a game..."}
        self.win_history: list[tuple[float, float]] = []  # (game time, win probability), for the chart
        self.wake = threading.Event()  # set by a seek or tier change: recompute now, don't wait out the sleep

    def add_win_point(self, game_time: float, p_win: float) -> None:
        """Called with the lock held; a clock that goes backwards means a new game (or replay)"""
        if self.win_history and game_time < self.win_history[-1][0]:
            self.win_history = []
        if not self.win_history or game_time >= self.win_history[-1][0] + WIN_HISTORY_STEP_S:
            self.win_history.append((round(game_time, 1), p_win))


def poll_loop(source, recommender: LiveRecommender, state: State) -> None:
    computed_for = None  # (game time, tier) of the current result: no need to recompute the same thing
    while True:
        snapshot = source.fetch()
        with state.lock:
            tier = state.tier
        key = (snapshot["gameData"]["gameTime"], tier) if snapshot else None
        if key is None or key != computed_for:
            compute(snapshot, tier, recommender, state)
            computed_for = key
        state.wake.wait(POLL_INTERVAL)
        state.wake.clear()


def compute(snapshot: dict | None, tier: str, recommender: LiveRecommender, state: State) -> None:
    try:
        result = ({"status": "waiting", "message": "Waiting for a game..."} if snapshot is None
            else {"status": "ok", **recommender.recommend(snapshot, tier)})
    except UnknownPlayer as exc:
        result = {"status": "waiting", "message": str(exc)}
    except Exception as exc:  # keep serving; show the problem on the page
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
    with state.lock:
        state.latest = result
        if result["status"] == "ok":
            state.add_win_point(result["game_time"], result["win_probability"])


class TierRequest(BaseModel):
    tier: Literal[TIERS]


class PauseRequest(BaseModel):
    paused: bool


class SeekRequest(BaseModel):
    seconds: float = Field(ge=0)


class SpeedRequest(BaseModel):
    speed: Literal[REPLAY_SPEEDS]


def create_app(state: State, replay: ReplaySource | None = None) -> FastAPI:
    app = FastAPI(title="Item Recommender")

    def require_replay() -> ReplaySource:
        if replay is None:
            raise HTTPException(404, "not replaying a recording")
        return replay

    @app.get("/api/state")
    def get_state() -> dict:
        with state.lock:
            return {**state.latest, "tiers": TIERS, "selected_tier": state.tier, "win_history": state.win_history,
                "replay": replay.status() if replay else None, "replay_speeds": REPLAY_SPEEDS}

    @app.post("/api/tier")
    def set_tier(body: TierRequest) -> None:
        with state.lock:
            state.tier = body.tier
        state.wake.set()

    @app.post("/api/replay/pause")
    def pause(body: PauseRequest) -> None:
        require_replay().set_paused(body.paused)

    @app.post("/api/replay/seek")
    def seek(body: SeekRequest) -> None:
        require_replay().seek(body.seconds)
        with state.lock:
            state.win_history = []
        state.wake.set()

    @app.post("/api/replay/speed")
    def speed(body: SpeedRequest) -> None:
        require_replay().set_speed(body.speed)

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True))
    else:
        @app.get("/")
        def not_built() -> PlainTextResponse:
            return PlainTextResponse("The page isn't built yet: cd web && npm install && npm run build", 503)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replay", help="'latest' or a recorded .jsonl.gz to replay instead of the live game")
    parser.add_argument("--speed", type=float, default=10.0, help="replay speed multiplier")
    parser.add_argument("--start-minute", type=float, default=0.0, help="replay from this game minute")
    parser.add_argument("--model-dir", type=Path, default=MODELS_DIR / "full")
    parser.add_argument("--model", default="imitation", help="imitation | awr_beta1 | baseline")
    parser.add_argument("--tier", default="EMERALD", choices=TIERS, help="rank whose choices to imitate")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    catalog = Catalog.load(STATIC_DIR)
    recommender = LiveRecommender(args.model_dir, catalog, args.model)
    if recommender.patch_warning:
        print("WARNING:", recommender.patch_warning)
    if args.replay:
        recording = resolve_recording(args.replay)
        source = ReplaySource(recording, args.speed, args.start_minute)
        print(f"Replaying {recording.name} from minute {args.start_minute:g} at x{args.speed:g}")
    else:
        source = LiveClient()
    state = State(args.tier)
    threading.Thread(target=poll_loop, args=(source, recommender, state), daemon=True).start()
    app = create_app(state, source if isinstance(source, ReplaySource) else None)
    print(f"Serving on http://localhost:{args.port}  ({'replay x%g' % args.speed if args.replay else 'live game'}, "
        f"model {args.model} from {args.model_dir})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
