from recommender.catalog import Catalog

SR = {"11": True, "12": True}


def _item(name, gold, *, into=(), from_=(), depth=1, tags=(), stats=None, maps=SR, purchasable=True, **extra):
    return {"name": name, "gold": {"total": gold, "purchasable": purchasable}, "into": list(into),
            "from": list(from_), "depth": depth, "tags": list(tags), "stats": stats or {}, "maps": maps, **extra}


ITEM_JSON = {"version": "16.20.1", "data": {
    "1001": _item("Boots", 300, into=("3006",), tags=("Boots",)),
    "3006": _item("Berserker's Greaves", 1100, from_=("1001",), into=("3172",), depth=2, tags=("Boots",)),
    "3172": _item("Gunmetal Greaves", 1100, from_=("3006",), depth=3, tags=("Boots",), purchasable=False),
    "1036": _item("Long Sword", 350, into=("3031",), stats={"FlatPhysicalDamageMod": 10}),
    "1029": _item("Cloth Armor", 300, into=("3075",), stats={"FlatArmorMod": 15}),
    "3031": _item("Infinity Edge", 3500, from_=("1036",), depth=3,
                  stats={"FlatPhysicalDamageMod": 65, "FlatCritChanceMod": 0.25}),
    "3075": _item("Thornmail", 2450, from_=("1029",), depth=2, stats={"FlatArmorMod": 75, "FlatHPPoolMod": 150}),
    "3089": _item("Rabadon's Deathcap", 3500, depth=3, stats={"FlatMagicDamageMod": 130}),
    "4403": _item("Golden Spatula", 7000, maps={"11": False, "30": True}),  # Arena only
    "3599": _item("Kalista's Black Spear", 0, requiredChampion="Kalista"),
    "2055": _item("Control Ward", 75, tags=("Consumable",)),
    "2421": _item("Shattered Armguard", 1600),
}}


def _champ(id_, key, name, tags, attack, defense, magic):
    return {"id": id_, "key": str(key), "name": name, "tags": list(tags),
            "info": {"attack": attack, "defense": defense, "magic": magic, "difficulty": 5}}


CHAMPION_JSON = {"version": "16.20.1", "data": {
    "Garen": _champ("Garen", 86, "Garen", ("Fighter", "Tank"), 7, 7, 1),
    "Ahri": _champ("Ahri", 103, "Ahri", ("Mage", "Assassin"), 3, 4, 8),
    "MonkeyKing": _champ("MonkeyKing", 62, "Wukong", ("Fighter", "Tank"), 8, 5, 2),
    "Jinx": _champ("Jinx", 222, "Jinx", ("Marksman",), 9, 2, 4),
}}


SUMMONER_JSON = {"data": {
    "SummonerFlash": {"id": "SummonerFlash", "key": "4", "modes": ["CLASSIC"]},
    "SummonerDot": {"id": "SummonerDot", "key": "14", "modes": ["CLASSIC"]},
    "SummonerTeleport": {"id": "SummonerTeleport", "key": "12", "modes": ["CLASSIC"]},
    "SummonerSnowball": {"id": "SummonerSnowball", "key": "32", "modes": ["ARAM"]},
}}
RUNES_JSON = [
    {"key": "Precision", "slots": [{"runes": [{"id": 8010, "key": "Conqueror"}, {"id": 8008, "key": "LethalTempo"}]},
        {"runes": [{"id": 9111, "key": "Triumph"}]}]},
    {"key": "Domination", "slots": [{"runes": [{"id": 8112, "key": "Electrocute"}]}]},
]


def catalog() -> Catalog:
    return Catalog.from_json(ITEM_JSON, CHAMPION_JSON, SUMMONER_JSON, RUNES_JSON)
