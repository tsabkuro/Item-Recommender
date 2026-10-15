"""Live snapshot -> recommendation
"""
import time
from pathlib import Path

import joblib
import polars as pl
from threadpoolctl import threadpool_limits

from live.adapter import to_game_state
from model.predictor import Recommender
from recommender.catalog import Catalog
from recommender.features import CATEGORICAL, FEATURE_VERSION, build_features
from recommender.state import PlayerState

ICON_URL = "https://ddragon.leagueoflegends.com/cdn/{version}/img/{kind}/{name}.png"
TAG_LABELS = {"Damage": "Attack damage", "SpellDamage": "Ability power", "CriticalStrike": "Crit",
    "AttackSpeed": "Attack speed", "OnHit": "On-hit", "ArmorPenetration": "Armor pen",
    "MagicPenetration": "Magic pen", "AbilityHaste": "Ability haste", "LifeSteal": "Life steal",
    "SpellVamp": "Omnivamp", "Health": "Health", "Armor": "Armor", "SpellBlock": "Magic resist",
    "Mana": "Mana", "Tenacity": "Tenacity", "NonbootsMovement": "Move speed", "Slow": "Slow"}


class IncompatibleModel(RuntimeError):
    pass


class LiveRecommender:
    def __init__(self, model_dir: Path, catalog: Catalog, model_name: str = "imitation"):
        self.catalog = catalog
        self.rec = Recommender.load(model_dir / f"{model_name}.joblib")
        win = joblib.load(model_dir / "win.joblib")
        self.win_model, self.win_encoder = win["model"], win["encoder"]
        trained_version = self.rec.meta.get("feature_version")
        if trained_version != FEATURE_VERSION:
            raise IncompatibleModel(f"model trained with feature version {trained_version}, "
                f"code is at {FEATURE_VERSION}: retrain before serving")
        self.patch_warning = None
        if self.rec.meta.get("catalog_version") != catalog.version:
            self.patch_warning = (f"model trained on Data Dragon {self.rec.meta.get('catalog_version')}, "
                f"game data is {catalog.version}")

    def recommend(self, snapshot: dict, tier: str, k: int = 3) -> dict:
        started = time.perf_counter()
        state = to_game_state(snapshot, self.catalog, tier)
        features = build_features(state, self.catalog)
        df = pl.DataFrame([features], schema_overrides={c: pl.String for c in CATEGORICAL})
        # never suggest boots to an ADC
        exclude = self.catalog.tier2_boots if state.my.role == "BOTTOM" else frozenset()
        items, probs = self.rec.recommend(df, self.catalog, k, exclude)
        with threadpool_limits(1):
            p_win = float(self.win_model.predict_proba(self.win_encoder.transform(df))[0, 1])
        me = state.my
        recs = []
        for item_id, p in zip(items[0].tolist(), probs[0].tolist()):
            item = self.catalog.items.get(item_id)
            tags = [label for tag, label in TAG_LABELS.items() if item and tag in item.tags]
            recs.append({**self._item_view(item_id), "cost": item.gold if item else 0,
                "remaining": self.catalog.remaining_cost(item_id, me.items),
                "probability": round(p, 3), "tags": tags[:3]})
        return {
            "game_time": state.game_time, "champion": me.champion, "role": me.role, "tier": tier,
            "gold": round(state.my_gold), "level": me.level, "kda": [me.kills, me.deaths, me.assists],
            "champion_icon": self._icon("champion", me.champion), "cs": me.cs,
            "win_probability": round(p_win, 3),
            "team_item_value_diff": features["team_item_value_diff"],
            "enemy": {"attack": features["enemy_attack"], "magic": features["enemy_magic"],
                "armor": features["enemy_armor"], "mr": features["enemy_mr"]},
            "recommendations": recs,
            "players": [self._player_view(p, i == state.me) for i, p in enumerate(state.players)],
            "my_team": me.team, "model": self.rec.name, "patch": self.catalog.version,
            "patch_warning": self.patch_warning,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def _icon(self, kind: str, name) -> str:
        return ICON_URL.format(version=self.catalog.version, kind=kind, name=name)

    def _item_view(self, item_id: int) -> dict:
        item = self.catalog.items.get(item_id)
        return {"item_id": item_id, "name": item.name if item else str(item_id), "icon": self._icon("item", item_id)}

    def _player_view(self, p: PlayerState, is_me: bool) -> dict:
        """What the page shows per player"""
        champ = self.catalog.champion_by_name(p.champion)
        items = list(dict.fromkeys(p.items))  # the API lists stacks (e.g. 2 Control Wards) once
        trinkets = [i for i in items if i in self.catalog.trinkets]
        items = [i for i in items if i not in trinkets]
        return {"champion": champ.name if champ else p.champion, "icon": self._icon("champion", p.champion),
            "team": p.team, "role": p.role, "level": p.level, "kda": [p.kills, p.deaths, p.assists],
            "cs": p.cs, "is_me": is_me, "items": [self._item_view(i) for i in items],
            "trinket": self._item_view(trinkets[0]) if trinkets else None}
