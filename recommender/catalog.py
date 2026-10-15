"""Static game knowledge from Data Dragon: items, champions, and the label space

Both the training pipeline and the live app load the same Catalog, so they agree on
what counts as a "finished" item, what an item is worth, and which champion is which
"""
import json
from dataclasses import dataclass
from pathlib import Path

SUMMONERS_RIFT = "11"
# Cheapest legendary is 2200g. Everything with no upgrade path at or above this is "finished"
MIN_FINISHED_GOLD = 2200

# Data Dragon stat names -> short feature names
STAT_NAMES = {
    "FlatPhysicalDamageMod": "ad",
    "FlatMagicDamageMod": "ap",
    "FlatArmorMod": "armor",
    "FlatSpellBlockMod": "mr",
    "FlatHPPoolMod": "hp",
    "PercentAttackSpeedMod": "attack_speed",
    "FlatCritChanceMod": "crit",
    "PercentLifeStealMod": "lifesteal",
}
CHAMPION_TAGS = ("Assassin", "Fighter", "Mage", "Marksman", "Support", "Tank")
# Items features ignore because training data and the live API disagree on them.
# (Items the live API hides instead, e.g. the ADC's boots, are handled in live/recommend.py)
SUPPORT_ITEM_ROOTS = {"World Atlas", "Runic Compass", "Bounty of Worlds"}
IGNORED_ITEM_NAMES = {"Total Biscuit of Everlasting Will", "Slightly Magical Footwear", "Shattered Armguard"}
# Items that transform once stacked. Data Dragon doesn't link these pairs
STACKING_TRANSFORMS = {
    3042: 3004,  # Muramana <- Manamune
    3040: 3003,  # Seraph's Embrace <- Archangel's Staff
    3121: 3119,  # Fimbulwinter <- Winter's Approach
}


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    gold: int
    stats: dict[str, float]  # short stat name -> value
    tags: tuple[str, ...]
    into: tuple[int, ...]
    from_: tuple[int, ...]
    depth: int
    required_champion: str | None


@dataclass(frozen=True)
class Champion:
    id: str         # Data Dragon id, e.g. "MonkeyKing"
    key: int        # numeric id used by Match-V5 (championId), e.g. 62
    name: str       # display name, e.g. "Wukong"
    tags: tuple[str, ...]
    attack: int     # Data Dragon "info" ratings, 0-10
    defense: int
    magic: int


class Catalog:
    def __init__(self, version: str, items: dict[int, Item], champions: dict[str, Champion],
            spells: dict[str, int] | None = None, keystones: set[int] | None = None,
            ignored_items: frozenset[int] = frozenset(), trinkets: frozenset[int] = frozenset()):
        self.version = version
        self.ignored_items = ignored_items
        self.trinkets = trinkets
        self.items = items
        self.champions = champions
        self.spells = spells or {}  # Data Dragon spell id ("SummonerDot") -> numeric key (14)
        self.keystones = tuple(sorted(keystones or ()))  # keystone rune ids, e.g. 8010 (Conqueror)
        self._by_key = {c.key: c for c in champions.values()}
        self._by_name = {_norm(c.name): c for c in champions.values()} | {_norm(c.id): c for c in champions.values()}

        self.tier2_boots = frozenset(i for i, it in items.items() if "Boots" in it.tags and it.depth == 2)
        # Boots of every tier (Data Dragon doesn't tag every tier-3 pair)
        self.boots = frozenset(i for i, it in items.items() if "Boots" in it.tags) | \
            {u for boots in self.tier2_boots for u in items[boots].into}
        # Upgrades the player never chooses or pays for: tier-3 boots and stacking transforms. Both count as the item that was bought: base -> upgrade
        upgrades = {boots: items[boots].into[0] for boots in self.tier2_boots if items[boots].into}
        upgrades |= {base: done for done, base in STACKING_TRANSFORMS.items() if base in items}
        upgrades |= {i: it.into[0] for i, it in items.items()
            if it.into and "Boots" not in it.tags and not any(t in items for t in it.into)}
        # Timelines record these only as "base item destroyed"; Inventory adds the upgrade
        self.silent_upgrades = upgrades
        self._canonical = {done: base for base, done in upgrades.items()}
        # Tier-2 boots can have more than one listed upgrade
        self._canonical |= {u: boots for boots in self.tier2_boots for u in items[boots].into}
        # Finished: costs a legendary's worth and can't be upgraded by buying something else
        legendaries = {i for i, it in items.items()
            if it.gold >= MIN_FINISHED_GOLD and "Boots" not in it.tags
            and not any(t in items for t in it.into)}
        # Candidate label space
        self.finished = tuple(sorted(legendaries | self.tier2_boots))
        self._finished_set = frozenset(self.finished)

    @classmethod
    def from_json(cls, item_json: dict, champion_json: dict, summoner_json: dict | None = None,
            runes_json: list | None = None) -> "Catalog":
        items = {}
        for raw_id, it in item_json["data"].items():
            if not (it.get("maps", {}).get(SUMMONERS_RIFT) and it.get("gold", {}).get("purchasable")
                    and it.get("inStore", True)):
                continue
            items[int(raw_id)] = Item(
                id=int(raw_id),
                name=it["name"],
                gold=it["gold"]["total"],
                stats={STAT_NAMES[k]: v for k, v in it.get("stats", {}).items() if k in STAT_NAMES},
                tags=tuple(it.get("tags", ())),
                into=tuple(int(x) for x in it.get("into", ())),
                from_=tuple(int(x) for x in it.get("from", ())),
                depth=it.get("depth", 1),
                required_champion=it.get("requiredChampion"),
            )
        champions = {
            c["id"]: Champion(id=c["id"], key=int(c["key"]), name=c["name"], tags=tuple(c["tags"]),
                              attack=c["info"]["attack"], defense=c["info"]["defense"], magic=c["info"]["magic"])
            for c in champion_json["data"].values()
        }
        spells = {s["id"]: int(s["key"]) for s in (summoner_json or {}).get("data", {}).values()
                  if "CLASSIC" in s.get("modes", ())}
        keystones = {r["id"] for tree in (runes_json or []) for r in tree["slots"][0]["runes"]}
        trinkets = frozenset(int(i) for i, it in item_json["data"].items() if "Trinket" in it.get("tags", ()))
        return cls(item_json.get("version", champion_json.get("version", "?")), items, champions, spells, keystones,
            _ignored_items(item_json["data"]), trinkets)

    @classmethod
    def load(cls, static_dir: Path) -> "Catalog":
        """Load from a Data Dragon snapshot dir"""
        def read(name):
            path = static_dir / name
            return json.loads(path.read_text()) if path.exists() else None

        return cls.from_json(read("item.json"), read("champion.json"), read("summoner.json"), read("runesReforged.json"))

    # ITEMS

    def canonical(self, item_id: int) -> int:
        return self._canonical.get(item_id, item_id)

    def is_finished(self, item_id: int) -> bool:
        return self.canonical(item_id) in self._finished_set

    def finished_owned(self, item_ids) -> list[int]:
        """Canonical finished items in an inventory (tier-3 boots reported as tier-2)"""
        return [self.canonical(i) for i in item_ids if self.is_finished(i)]

    def gold_value(self, item_ids) -> int:
        """Total gold spent on these items"""
        return sum(self.items[i].gold for i in item_ids if i in self.items)

    def remaining_cost(self, item_id: int, owned) -> int:
        """Gold still needed to buy item_id, crediting held components"""
        held = list(owned)

        def credit(i: int) -> int:  # gold of i's recipe already covered by held items
            if i in held:
                held.remove(i)
                return self.items[i].gold
            return sum(credit(c) for c in self.items[i].from_ if c in self.items)

        item = self.items.get(item_id)
        return item.gold - sum(credit(c) for c in item.from_ if c in self.items) if item else 0

    def stat_totals(self, item_ids) -> dict[str, float]:
        totals = dict.fromkeys(STAT_NAMES.values(), 0.0)
        for i in item_ids:
            item = self.items.get(i)
            if item:
                for stat, value in item.stats.items():
                    totals[stat] += value
        return totals

    # CHAMPIONS

    def champion_by_key(self, key: int) -> Champion | None:
        return self._by_key.get(key)

    def champion_by_name(self, name: str) -> Champion | None:
        """Accepts a display name ("Wukong") or Data Dragon id ("MonkeyKing")"""
        return self._by_name.get(_norm(name))


def _ignored_items(raw_items: dict) -> frozenset[int]:
    ignored = {int(i) for i, it in raw_items.items()
               if "Trinket" in it.get("tags", ()) or it.get("name") in IGNORED_ITEM_NAMES
               or "Quest" in it.get("name", "")}
    todo = [i for i, it in raw_items.items() if it.get("name") in SUPPORT_ITEM_ROOTS]
    while todo:
        i = todo.pop()
        if int(i) not in ignored:
            ignored.add(int(i))
            todo.extend(raw_items.get(i, {}).get("into", []))
    return frozenset(ignored)


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())
