"""Configs for crawler"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Region:
    platform: str  # e.g. "na1"
    regional: str  # e.g. "americas"


# Riot rate limits per region, so we can get 3x throughput
REGIONS = {
    "na": Region(platform="na1", regional="americas"),
    "euw": Region(platform="euw1", regional="europe"),
    "kr": Region(platform="kr", regional="asia"),
}

TIERS = ("SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND")
DIVISIONS = ("I", "II", "III", "IV")

QUEUE_TYPE = "RANKED_SOLO_5x5"
QUEUE_ID = 420

# API rate limits per region: (count, seconds)
# X-App-Rate-Limit / X-Method-Rate-Limit response headers override these at runtime
APP_LIMITS = ((1_200_000, 600.0),)
METHOD_LIMITS = {
    "league-v4.entries": ((50, 10.0),),
    "match-v5.ids": ((2000, 10.0),),
    "match-v5.match": ((2000, 10.0),),
    "match-v5.timeline": ((2000, 10.0),),
}
OTHER_METHOD_LIMITS = ((20, 1.0),)

# Use 100% of each limit, but stretch every window by this many seconds to absorb
# send-vs-arrival jitter (2.5% of a 10 s window). This is just in case because repeated 429 could be blacklisted
RATE_WINDOW_PAD_S = 0.25
WORKERS_PER_REGION = 64

# recent games per player, made small so data set gets many players rather than just a few very active gamers
MATCHES_PER_PLAYER = 20
MATCHLIST_LOOKBACK_DAYS = 14

# find more matches only if under this constant number in queue currently
MIN_QUEUED_MATCHES_PER_TIER = 500

MIN_GAME_DURATION_S = 600  # shorter games are remakes
MAX_MATCH_ATTEMPTS = 3
MAX_REQUEST_FAILURES = 5  # retries of one request (5xx, 429 without Retry-After) before giving up

DATA_DIR = Path("data")
STATIC_DIR = DATA_DIR / "static" / "current"  # local Data Dragon copy
LOG_DIR = DATA_DIR / "logs"
RECORDINGS_DIR = DATA_DIR / "live"           # recorded live games
MODELS_DIR = DATA_DIR / "models"
ENV_FILE = Path(".env")                      # holds RIOT_API_KEY
DB_FILENAME = "crawl.db"
MIN_FREE_DISK_GB = 15
STATS_INTERVAL_S = 300

# DDRAGON CONSTS
DDRAGON_URL = "https://ddragon.leagueoflegends.com"
STATIC_FILES = ("item.json", "champion.json", "summoner.json", "runesReforged.json")
VERSION_FILE = "version.txt"
CHECK_INTERVAL_S = 24 * 3600
