"""Live Client API snapshot -> GameState (the same shape training builds from timelines)

Format details:
    - teams are "ORDER" (blue, Match-V5 team 100) and "CHAOS" (red, team 200)
    - rawChampionName is "game_character_displayname_<Data Dragon id>"
    - items come with a stack count (2 Control Wards = one entry with count 2), while timeline
        replay holds one entry per copy, so counts are expanded
    - runes.keystone.id is the same perk id Match-V5 uses
    - summoner spells come as names, e.g. rawDisplayName
        "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName" -> "SummonerFlash" -> key 4
    - only the active player's gold is exposed (activePlayer.currentGold)
    - role-bound slots aren't shown: an ADC's boots never appear (from the moment they're bought),
        nor do supports' Control Wards
"""
from recommender.catalog import Catalog
from recommender.state import GameState, PlayerState

TEAMS = {"ORDER": 100, "CHAOS": 200}
RAW_CHAMPION_PREFIX = "game_character_displayname_"
SPELL_PREFIX = "GeneratedTip_SummonerSpell_"


class UnknownPlayer(ValueError):
    pass


def _champion(p: dict, catalog: Catalog) -> str:
    raw = p.get("rawChampionName", "")
    for name in (raw.removeprefix(RAW_CHAMPION_PREFIX), p.get("championName", "")):
        champ = catalog.champion_by_name(name) if name else None
        if champ:
            return champ.id
    return p.get("championName", "Unknown")


def _spell_keys(p: dict, catalog: Catalog) -> tuple[int, ...]:
    keys = []
    for spell in p.get("summonerSpells", {}).values():
        raw = spell.get("rawDisplayName", "")
        spell_id = raw.removeprefix(SPELL_PREFIX).split("_")[0]
        if spell_id in catalog.spells:
            keys.append(catalog.spells[spell_id])
    return tuple(keys)


def player_state(p: dict, catalog: Catalog) -> PlayerState:
    items = tuple(item["itemID"] for item in p.get("items", []) for _ in range(item.get("count", 1)))
    scores = p.get("scores", {})
    return PlayerState(
        champion=_champion(p, catalog), team=TEAMS.get(p.get("team"), 0), role=p.get("position", ""),
        items=items, level=p.get("level", 1), kills=scores.get("kills", 0), deaths=scores.get("deaths", 0),
        assists=scores.get("assists", 0), cs=scores.get("creepScore", 0),
        keystone=p.get("runes", {}).get("keystone", {}).get("id", 0), spells=_spell_keys(p, catalog))


def active_index(snapshot: dict) -> int:
    me = snapshot.get("activePlayer", {})
    for i, p in enumerate(snapshot.get("allPlayers", [])):
        for key in ("riotId", "summonerName"):
            if me.get(key) and p.get(key) == me.get(key):
                return i
    raise UnknownPlayer("active player not found among allPlayers (spectating?)")


def to_game_state(snapshot: dict, catalog: Catalog, tier: str) -> GameState:
    players = tuple(player_state(p, catalog) for p in snapshot["allPlayers"])
    return GameState(game_time=float(snapshot["gameData"]["gameTime"]), players=players,
        me=active_index(snapshot), my_gold=float(snapshot["activePlayer"].get("currentGold", 0.0)), tier=tier)

