"""Crawl state in SQLite: which players and matches are queued, done, or rejected

Shared by all region processes and their worker threads (one connection per thread)

Workers take work by *claiming* a row in a single UPDATE, so no two of them ever fetch
the same match
"""
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from crawler import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    region      TEXT NOT NULL,
    puuid       TEXT NOT NULL,
    tier        TEXT NOT NULL,
    division    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued | claimed | done | stale
    added_at    REAL NOT NULL,
    crawled_at  REAL,
    PRIMARY KEY (region, puuid)
);
CREATE INDEX IF NOT EXISTS players_queue ON players (region, tier, status);

CREATE TABLE IF NOT EXISTS matches (
    match_id       TEXT PRIMARY KEY,                -- not encrypted, stable across API keys
    region         TEXT NOT NULL,
    seed_tier      TEXT NOT NULL,                   -- tier of the player we found it through
    seed_division  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued',  -- queued | claimed | done | rejected | failed
    patch          TEXT,
    reject_reason  TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    discovered_at  REAL NOT NULL,
    updated_at     REAL
);
CREATE INDEX IF NOT EXISTS matches_queue ON matches (region, seed_tier, status);
CREATE INDEX IF NOT EXISTS matches_updated ON matches (status, updated_at);

CREATE TABLE IF NOT EXISTS seed_cursors (
    region     TEXT NOT NULL,
    tier       TEXT NOT NULL,
    division   TEXT NOT NULL,
    next_page  INTEGER NOT NULL DEFAULT 1,
    exhausted  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (region, tier, division)
);
"""


class CrawlState:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._local = threading.local()
        self.conn.executescript(SCHEMA)

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's connection (SQLite connections can't be shared between threads)"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _scalar(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def _claim(self, sql: str, params=()) -> tuple | None:
        # an UPDATE ... RETURNING only completes (and commits) when all rows have been read
        rows = self.conn.execute(sql, params).fetchall()
        return rows[0] if rows else None

    def release_claims(self, region: str) -> int:
        """Re-queue work a previous run claimed but never finished"""
        with self._tx() as c:
            before = c.total_changes
            c.execute("UPDATE matches SET status='queued' WHERE region=? AND status='claimed'", (region,))
            c.execute("UPDATE players SET status='queued' WHERE region=? AND status='claimed'", (region,))
            return c.total_changes - before

    # PLAYERS

    def add_players(self, region: str, tier: str, division: str, puuids: list[str]) -> int:
        now = time.time()
        with self._tx() as c:
            before = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO players (region, puuid, tier, division, added_at) VALUES (?, ?, ?, ?, ?)",
                [(region, p, tier, division, now) for p in puuids],
            )
            return c.total_changes - before

    def claim_player(self, region: str, tier: str) -> tuple[str, str] | None:
        return self._claim(
            "UPDATE players SET status='claimed' WHERE rowid = ("
            "  SELECT rowid FROM players WHERE region=? AND tier=? AND status='queued' ORDER BY added_at LIMIT 1"
            ") RETURNING puuid, division",
            (region, tier),
        )

    def mark_player(self, region: str, puuid: str, status: str) -> None:
        self.conn.execute(
            "UPDATE players SET status=?, crawled_at=? WHERE region=? AND puuid=?",
            (status, time.time(), region, puuid),
        )

    def mark_region_players_stale(self, region: str) -> int:
        """Drop the whole queue"""
        cur = self.conn.execute(
            "UPDATE players SET status='stale' WHERE region=? AND status IN ('queued', 'claimed')", (region,)
        )
        return cur.rowcount

    # SEEDING

    def claim_seed_page(self, region: str, tier: str) -> tuple[str, int] | None:
        """Take the next page of the least-paged, non-exhausted division of a tier"""
        self.conn.executemany(
            "INSERT OR IGNORE INTO seed_cursors (region, tier, division) VALUES (?, ?, ?)",
            [(region, tier, d) for d in config.DIVISIONS],
        )
        return self._claim(
            "UPDATE seed_cursors SET next_page = next_page + 1 WHERE rowid = ("
            "  SELECT rowid FROM seed_cursors WHERE region=? AND tier=? AND exhausted=0"
            "  ORDER BY next_page, division LIMIT 1"
            ") RETURNING division, next_page - 1",
            (region, tier),
        )

    def mark_seed_exhausted(self, region: str, tier: str, division: str) -> None:
        self.conn.execute(
            "UPDATE seed_cursors SET exhausted=1 WHERE region=? AND tier=? AND division=?",
            (region, tier, division),
        )

    # MATCHES

    def add_matches(self, region: str, tier: str, division: str, match_ids: list[str]) -> int:
        now = time.time()
        with self._tx() as c:
            before = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO matches (match_id, region, seed_tier, seed_division, discovered_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(m, region, tier, division, now) for m in match_ids],
            )
            return c.total_changes - before

    def queued_match_count(self, region: str, tier: str) -> int:
        return self._scalar(
            "SELECT COUNT(*) FROM matches WHERE region=? AND seed_tier=? AND status='queued'", (region, tier)
        )

    def claim_match(self, region: str, tier: str) -> str | None:
        row = self._claim(
            "UPDATE matches SET status='claimed', updated_at=? WHERE match_id = ("
            "  SELECT match_id FROM matches WHERE region=? AND seed_tier=? AND status='queued'"
            "  ORDER BY discovered_at LIMIT 1"
            ") RETURNING match_id",
            (time.time(), region, tier),
        )
        return row[0] if row else None

    def tiers_by_progress(self, region: str) -> list[str]:
        """Tiers ordered by fewest completed matches first to stay balanced"""
        done = dict(self.conn.execute(
            "SELECT seed_tier, COUNT(*) FROM matches WHERE region=? AND status='done' GROUP BY seed_tier",
            (region,),
        ).fetchall())
        return sorted(config.TIERS, key=lambda t: done.get(t, 0))

    def mark_match_done(self, match_id: str, patch: str) -> None:
        self.conn.execute(
            "UPDATE matches SET status='done', patch=?, updated_at=? WHERE match_id=?",
            (patch, time.time(), match_id),
        )

    def mark_match_rejected(self, match_id: str, reason: str, patch: str | None = None) -> None:
        self.conn.execute(
            "UPDATE matches SET status='rejected', reject_reason=?, patch=?, updated_at=? WHERE match_id=?",
            (reason, patch, time.time(), match_id),
        )

    def record_match_failure(self, match_id: str, max_attempts: int) -> str:
        """Count a failed attempt and re-queue the match. give up (status 'failed') after max_attempts"""
        self.conn.execute(
            "UPDATE matches SET attempts = attempts + 1, updated_at=?, "
            "status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'queued' END WHERE match_id=?",
            (time.time(), max_attempts, match_id),
        )
        return self._scalar("SELECT status FROM matches WHERE match_id=?", (match_id,))
