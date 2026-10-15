"""GameState -> model features

Training (pipeline/gold.py) and serving (live/recommend.py) both call build_features

the model sees:
  - me: champion, role, minute, gold, level, KDA, CS, which finished items I own
  - enemy team: damage profile from champion ratings, class mix (tanks, assassins...),
    stats of the items they're holding (armor, MR, AD, AP...)
  - who's ahead: team item-value and kill differences (item value stands in for gold)
"""
from dataclasses import replace
from statistics import mean

from recommender.catalog import CHAMPION_TAGS, STAT_NAMES, Catalog
from recommender.state import GameState

FEATURE_VERSION = 4
CATEGORICAL = ("champion", "role", "tier", "keystone")
# summoner spells worth a flag (Flash is near-universal, so it says nothing)
SPELL_FEATURES = {"SummonerDot": "ignite", "SummonerTeleport": "teleport", "SummonerSmite": "smite",
    "SummonerExhaust": "exhaust", "SummonerHeal": "heal", "SummonerBarrier": "barrier",
    "SummonerBoost": "cleanse", "SummonerHaste": "ghost"}


def build_features(state: GameState, catalog: Catalog) -> dict[str, float | str]:
    # free items are invisible in training data but present live, so every item-based feature ignores them (see catalog.ignored_items)
    state = replace(state, players=tuple(
        replace(p, items=tuple(i for i in p.items if i not in catalog.ignored_items)) for p in state.players))
    me = state.my
    owned = set(catalog.finished_owned(me.items))
    allies = [me, *state.allies()]
    enemies = state.enemies()

    f: dict[str, float | str] = {
        "champion": me.champion,
        "role": me.role or "UNKNOWN",
        "tier": state.tier,
        "keystone": str(me.keystone),
        "minute": state.game_time / 60,
        "gold": state.my_gold,
        "level": me.level,
        "kills": me.kills,
        "deaths": me.deaths,
        "assists": me.assists,
        "cs": me.cs // 10 * 10, # live API rounds CS down in 10s so round training data too
        "item_value": catalog.gold_value(me.items),
        "n_finished": len(owned),
        "has_boots": int(bool(owned & catalog.tier2_boots)),
    }
    for spell_id, short in SPELL_FEATURES.items():
        f[f"spell_{short}"] = int(catalog.spells.get(spell_id) in me.spells)
    for item_id in catalog.finished:
        f[f"own_{item_id}"] = int(item_id in owned)

    ally_value = sum(catalog.gold_value(p.items) for p in allies)
    enemy_value = sum(catalog.gold_value(p.items) for p in enemies)
    f["enemy_item_value"] = enemy_value
    f["team_item_value_diff"] = ally_value - enemy_value
    f["team_kill_diff"] = sum(p.kills for p in allies) - sum(p.kills for p in enemies)
    f["team_level_diff"] = (mean(p.level for p in allies) - mean(p.level for p in enemies)) if enemies else 0.0

    enemy_champs = [c for c in (catalog.champion_by_name(p.champion) for p in enemies) if c]
    f["enemy_attack"] = sum(c.attack for c in enemy_champs)
    f["enemy_magic"] = sum(c.magic for c in enemy_champs)
    f["enemy_defense"] = sum(c.defense for c in enemy_champs)
    for tag in CHAMPION_TAGS:
        f[f"enemy_tag_{tag.lower()}"] = sum(tag in c.tags for c in enemy_champs)

    enemy_stats = catalog.stat_totals(i for p in enemies for i in p.items)
    for stat, value in enemy_stats.items():
        f[f"enemy_{stat}"] = value
    for keystone in catalog.keystones:
        f[f"enemy_keystone_{keystone}"] = sum(p.keystone == keystone for p in enemies)
    return f


def feature_names(catalog: Catalog) -> list[str]:
    """Stable feature order for a given catalog"""
    names = ["champion", "role", "tier", "keystone", "minute", "gold", "level", "kills", "deaths", "assists",
             "cs", "item_value", "n_finished", "has_boots"]
    names += [f"spell_{short}" for short in SPELL_FEATURES.values()]
    names += [f"own_{i}" for i in catalog.finished]
    names += ["enemy_item_value", "team_item_value_diff", "team_kill_diff", "team_level_diff",
              "enemy_attack", "enemy_magic", "enemy_defense"]
    names += [f"enemy_tag_{t.lower()}" for t in CHAMPION_TAGS]
    names += [f"enemy_{s}" for s in STAT_NAMES.values()]
    names += [f"enemy_keystone_{k}" for k in catalog.keystones]
    return names
