"""The canonical game state

Training builds it from Match-V5 timelines (pipeline/gold.py)
The live app builds it from Live Client API snapshots (live/adapter.py)

Features are computed only from this type, so treaining and serving is consistent
"""
from dataclasses import dataclass

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


@dataclass(frozen=True)
class PlayerState:
    champion: str                   # Data Dragon champion id, e.g. "MonkeyKing"
    team: int                       # 100 or 200
    role: str                       # one of ROLES
    items: tuple[int, ...]
    level: int
    kills: int
    deaths: int
    assists: int
    cs: int
    keystone: int = 0               # keystone rune id
    spells: tuple[int, ...] = ()    # summoner spell keys


@dataclass(frozen=True)
class GameState:
    game_time: float
    players: tuple[PlayerState, ...]
    me: int                             # index into players of the player we recommend for
    my_gold: float
    tier: str                           # rank to imitate from the match in training

    @property
    def my(self) -> PlayerState:
        return self.players[self.me]

    def allies(self) -> list[PlayerState]:
        return [p for i, p in enumerate(self.players) if p.team == self.my.team and i != self.me]

    def enemies(self) -> list[PlayerState]:
        return [p for p in self.players if p.team != self.my.team]
