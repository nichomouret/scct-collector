"""
Couche de stockage SCCT-collector.
- Par défaut : SQLite local (DB_PATH, défaut ./scct_posts.db).
- Si DATABASE_URL (postgres://...) est défini (ex. Railway Postgres) : Postgres.

Deux tables :
  posts    — collecte Reddit directe (en réserve, si approbation API Reddit).
  mentions — série temporelle de mentions/sentiment par ticker depuis les
             fournisseurs tiers (ApeWisdom, Adanos, Quiver). Voie principale.
INSERT idempotent (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations
import os, json, sqlite3
from typing import Iterable

DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    subreddit TEXT, listing TEXT,
    created_utc REAL, fetched_utc REAL,
    author TEXT, author_created_utc REAL,
    author_comment_karma INTEGER, author_link_karma INTEGER,
    score INTEGER, upvote_ratio REAL, num_comments INTEGER,
    flair TEXT, title TEXT, tickers TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);

CREATE TABLE IF NOT EXISTS mentions (
    source TEXT, ticker TEXT, fetched_utc REAL,
    rank INTEGER, mentions INTEGER, mentions_prev INTEGER,
    upvotes INTEGER, sentiment REAL, name TEXT, extra TEXT,
    PRIMARY KEY (source, ticker, fetched_utc)
);
CREATE INDEX IF NOT EXISTS idx_mentions_tk ON mentions(ticker, fetched_utc);
CREATE INDEX IF NOT EXISTS idx_mentions_fetched ON mentions(fetched_utc);

CREATE TABLE IF NOT EXISTS ticker_meta (
    ticker TEXT PRIMARY KEY,
    float_m REAL, si REAL, si_usd REAL, name TEXT, updated_utc REAL
);

CREATE TABLE IF NOT EXISTS tracked (
    ticker TEXT PRIMARY KEY,
    name TEXT,
    first_utc REAL, first_price REAL,
    last_active_utc REAL, last_seen_utc REAL,
    peak_score REAL, peak_utc REAL,
    last_score REAL, last_price REAL,
    status TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    ticker TEXT PRIMARY KEY,
    last_utc REAL
);
"""

TRACKED_COLS = ["ticker", "name", "first_utc", "first_price", "last_active_utc",
                "last_seen_utc", "peak_score", "peak_utc", "last_score",
                "last_price", "status"]

DDL_PG = DDL_SQLITE.replace("REAL", "DOUBLE PRECISION")

POST_COLS = ["post_id", "subreddit", "listing", "created_utc", "fetched_utc", "author",
             "author_created_utc", "author_comment_karma", "author_link_karma",
             "score", "upvote_ratio", "num_comments", "flair", "title", "tickers"]

MENTION_COLS = ["source", "ticker", "fetched_utc", "rank", "mentions",
                "mentions_prev", "upvotes", "sentiment", "name", "extra"]


class Store:
    def __init__(self):
        self.url = os.getenv("DATABASE_URL", "")
        self.is_pg = self.url.startswith(("postgres://", "postgresql://"))
        if self.is_pg:
            import psycopg2  # lazy
            self.conn = psycopg2.connect(self.url.replace("postgres://", "postgresql://", 1))
            self.ph = "%s"
            self._exec_script(DDL_PG)
        else:
            path = os.getenv("DB_PATH", "scct_posts.db")
            self.conn = sqlite3.connect(path)
            self.ph = "?"
            self._exec_script(DDL_SQLITE)
        self.conn.commit()

    def _exec_script(self, script: str):
        cur = self.conn.cursor()
        if self.is_pg:
            for stmt in filter(str.strip, script.split(";")):
                cur.execute(stmt)
        else:
            cur.executescript(script)

    def _upsert(self, table: str, cols: list[str], rows: Iterable[dict]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        cur = self.conn.cursor()
        placeholders = ", ".join([self.ph] * len(cols))
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT DO NOTHING")
        n = 0
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c)
                if isinstance(v, (dict, list)):
                    v = json.dumps(v)
                vals.append(v)
            cur.execute(sql, vals)
            n += cur.rowcount if (cur.rowcount and cur.rowcount > 0) else 0
        self.conn.commit()
        return n

    def upsert_posts(self, rows: Iterable[dict]) -> int:
        return self._upsert("posts", POST_COLS, rows)

    def upsert_mentions(self, rows: Iterable[dict]) -> int:
        return self._upsert("mentions", MENTION_COLS, rows)

    # rétro-compat
    def upsert(self, rows: Iterable[dict]) -> int:
        return self.upsert_posts(rows)

    def meta_get(self, ticker: str, max_age_days: float = 7.0):
        """Renvoie le cache float/SI d'un ticker s'il est récent, sinon None."""
        import time as _t
        cur = self.conn.cursor()
        cur.execute(f"SELECT ticker, float_m, si, si_usd, name, updated_utc "
                    f"FROM ticker_meta WHERE ticker = {self.ph}", (ticker.upper(),))
        row = cur.fetchone()
        if not row:
            return None
        updated = row[5] or 0
        if (_t.time() - updated) > max_age_days * 86400:
            return None
        return {"float_m": row[1], "si": row[2], "si_usd": row[3], "name": row[4]}

    def meta_put(self, ticker: str, float_m, si, si_usd, name):
        import time as _t
        cur = self.conn.cursor()
        sql = (f"INSERT INTO ticker_meta (ticker, float_m, si, si_usd, name, updated_utc) "
               f"VALUES ({self.ph},{self.ph},{self.ph},{self.ph},{self.ph},{self.ph}) "
               f"ON CONFLICT (ticker) DO UPDATE SET float_m=excluded.float_m, si=excluded.si, "
               f"si_usd=excluded.si_usd, name=excluded.name, updated_utc=excluded.updated_utc")
        cur.execute(sql, (ticker.upper(), float_m, si, si_usd, name, _t.time()))
        self.conn.commit()

    # --- suivi (tracking) des titres détectés ---
    def tracked_all(self) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(f"SELECT {', '.join(TRACKED_COLS)} FROM tracked")
        return [dict(zip(TRACKED_COLS, row)) for row in cur.fetchall()]

    def tracked_upsert(self, rec: dict):
        cur = self.conn.cursor()
        ph = ", ".join([self.ph] * len(TRACKED_COLS))
        updates = ", ".join(f"{c}=excluded.{c}" for c in TRACKED_COLS if c != "ticker")
        sql = (f"INSERT INTO tracked ({', '.join(TRACKED_COLS)}) VALUES ({ph}) "
               f"ON CONFLICT (ticker) DO UPDATE SET {updates}")
        rec = {**rec, "ticker": rec["ticker"].upper()}
        cur.execute(sql, [rec.get(c) for c in TRACKED_COLS])
        self.conn.commit()

    def tracked_delete(self, ticker: str):
        cur = self.conn.cursor()
        cur.execute(f"DELETE FROM tracked WHERE ticker = {self.ph}", (ticker.upper(),))
        self.conn.commit()

    # --- anti-spam des alertes ---
    def alert_recent(self, ticker: str, cooldown_h: float = 24.0) -> bool:
        """True si le ticker a déjà été alerté dans les cooldown_h dernières heures."""
        import time as _t
        cur = self.conn.cursor()
        cur.execute(f"SELECT last_utc FROM alerts WHERE ticker = {self.ph}", (ticker.upper(),))
        row = cur.fetchone()
        return bool(row and row[0] and (_t.time() - row[0]) < cooldown_h * 3600)

    def alert_mark(self, ticker: str):
        import time as _t
        cur = self.conn.cursor()
        cur.execute(f"INSERT INTO alerts (ticker, last_utc) VALUES ({self.ph},{self.ph}) "
                    f"ON CONFLICT (ticker) DO UPDATE SET last_utc=excluded.last_utc",
                    (ticker.upper(), _t.time()))
        self.conn.commit()

    def count(self, table: str = "posts") -> int:
        cur = self.conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
