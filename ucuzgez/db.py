"""SQLite katmanı (50 kullanıcı için fazlasıyla yeterli; WAL modunda)."""
from __future__ import annotations

import json
import secrets
import sqlite3
import statistics
import threading
import time
from contextlib import contextmanager
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,          -- web girişi
    tg_invite TEXT UNIQUE NOT NULL,      -- Telegram'da /start <kod>
    tg_chat_id INTEGER UNIQUE,
    home_lat REAL, home_lon REAL,
    home_radius_km REAL DEFAULT 150,
    home_code TEXT,                      -- örn IST (konum yoksa)
    is_admin INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    params TEXT NOT NULL,                -- TripQuery JSON
    threshold REAL,                      -- bu fiyatın altına düşerse haber ver
    active INTEGER DEFAULT 1,
    last_checked REAL DEFAULT 0,
    last_best REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_history (
    watch_id INTEGER NOT NULL, price REAL NOT NULL, at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wh ON watch_history(watch_id, at);
CREATE TABLE IF NOT EXISTS notified (
    user_id INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, price REAL NOT NULL, at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notified ON notified(user_id, kind, ref);
CREATE TABLE IF NOT EXISTS price_history (
    route TEXT NOT NULL,                 -- IST-VIE
    dep_date TEXT NOT NULL,
    price REAL NOT NULL,
    seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ph ON price_history(route, seen_at);
CREATE TABLE IF NOT EXISTS fare_cache (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class DB:
    def __init__(self, path: str, turso_url: str = "", turso_token: str = ""):
        self.path = path
        self.turso = (turso_url, turso_token) if turso_url else None
        self._local = threading.local()
        with self.tx() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None and self.turso:
            from .libsql_http import LibsqlConnection
            c = self._local.conn = LibsqlConnection(*self.turso)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    @contextmanager
    def tx(self):
        c = self._conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, args).fetchone()

    # ---- önbellek (providers.base.Cache arayüzü) -------------------------
    def cache_get(self, key: str, max_age_s: float) -> str | None:
        r = self.one("SELECT value, at FROM fare_cache WHERE key=?", (key,))
        if r and time.time() - r["at"] <= max_age_s:
            return r["value"]
        return None

    def cache_set(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO fare_cache(key,value,at) VALUES(?,?,?)", (key, value, time.time()))

    def cache_purge(self, older_than_s: float = 3 * 86400) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM fare_cache WHERE at < ?", (time.time() - older_than_s,))
            c.execute("DELETE FROM price_history WHERE seen_at < ?", (time.time() - 90 * 86400,))

    # ---- kullanıcılar ------------------------------------------------------
    def add_user(self, name: str, is_admin: bool = False) -> sqlite3.Row:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO users(name, token, tg_invite, is_admin, created_at) VALUES(?,?,?,?,?)",
                (name, secrets.token_urlsafe(24), secrets.token_hex(4), int(is_admin), time.time()))
        return self.one("SELECT * FROM users WHERE id=?", (cur.lastrowid,))

    def count_users(self) -> int:
        return self.one("SELECT COUNT(*) n FROM users")["n"]

    def user_by_token(self, token: str):
        return self.one("SELECT * FROM users WHERE token=?", (token,)) if token else None

    def user_by_chat(self, chat_id: int):
        return self.one("SELECT * FROM users WHERE tg_chat_id=?", (chat_id,))

    def user(self, uid: int):
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def link_telegram(self, invite: str, chat_id: int):
        u = self.one("SELECT * FROM users WHERE tg_invite=?", (invite,))
        if not u:
            return None
        if u["tg_chat_id"] and u["tg_chat_id"] != chat_id:
            return None          # kod zaten başka bir Telegram hesabına bağlı (tek kullanımlık)
        with self.tx() as c:
            c.execute("UPDATE users SET tg_chat_id=NULL WHERE tg_chat_id=?", (chat_id,))
            c.execute("UPDATE users SET tg_chat_id=? WHERE id=?", (chat_id, u["id"]))
        return self.user(u["id"])

    def set_home(self, uid: int, lat: float | None = None, lon: float | None = None,
                 code: str | None = None, radius_km: float | None = None) -> None:
        with self.tx() as c:
            if lat is not None:
                c.execute("UPDATE users SET home_lat=?, home_lon=? WHERE id=?", (lat, lon, uid))
            if code is not None:
                c.execute("UPDATE users SET home_code=? WHERE id=?", (code.upper() or None, uid))
            if radius_km is not None:
                c.execute("UPDATE users SET home_radius_km=? WHERE id=?", (radius_km, uid))

    def all_users(self):
        return self.q("SELECT * FROM users ORDER BY id")

    # ---- takipler ------------------------------------------------------------
    def add_watch(self, uid: int, name: str, params: dict, threshold: float | None) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO watches(user_id,name,params,threshold,created_at) VALUES(?,?,?,?,?)",
                (uid, name, json.dumps(params, ensure_ascii=False), threshold, time.time()))
        return cur.lastrowid

    def watches(self, uid: int):
        return self.q("SELECT * FROM watches WHERE user_id=? ORDER BY id", (uid,))

    def delete_watch(self, uid: int, wid: int) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM watches WHERE id=? AND user_id=?", (wid, uid)).rowcount > 0

    def due_watches(self, interval_s: float):
        return self.q("SELECT * FROM watches WHERE active=1 AND last_checked < ? ORDER BY last_checked",
                      (time.time() - interval_s,))

    def watch_checked(self, wid: int, best: float | None) -> None:
        with self.tx() as c:
            c.execute("UPDATE watches SET last_checked=?, last_best=? WHERE id=?", (time.time(), best, wid))
            if best is not None:
                c.execute("INSERT INTO watch_history VALUES(?,?,?)", (wid, best, time.time()))

    def watch_median(self, wid: int, days: int = 30) -> tuple[float | None, int]:
        rows = self.q("SELECT price FROM watch_history WHERE watch_id=? AND at>?",
                      (wid, time.time() - days * 86400))
        prices = [r["price"] for r in rows]
        return (statistics.median(prices) if prices else None), len(prices)

    # ---- bildirim tekrarını önleme -------------------------------------------
    def last_notified(self, uid: int, kind: str, ref: str, within_s: float):
        return self.one("SELECT * FROM notified WHERE user_id=? AND kind=? AND ref=? AND at>? "
                        "ORDER BY at DESC LIMIT 1", (uid, kind, ref, time.time() - within_s))

    def mark_notified(self, uid: int, kind: str, ref: str, price: float) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO notified VALUES(?,?,?,?,?)", (uid, kind, ref, price, time.time()))

    # ---- fiyat geçmişi (fırsat radarı) ---------------------------------------
    def record_prices(self, rows: list[tuple[str, str, float]]) -> None:
        now = time.time()
        with self.tx() as c:
            c.executemany("INSERT INTO price_history VALUES(?,?,?,?)", [(r, d, p, now) for r, d, p in rows])

    def route_median(self, route: str, days: int = 30) -> tuple[float | None, int]:
        """Rota için son N günde görülen GÜNLÜK en düşük fiyatların medyanı."""
        rows = self.q("SELECT MIN(price) p FROM price_history WHERE route=? AND seen_at>? "
                      "GROUP BY CAST(seen_at/86400 AS INT), dep_date",
                      (route, time.time() - days * 86400))
        prices = [r["p"] for r in rows]
        return (statistics.median(prices) if prices else None), len(prices)

    # ---- basit anahtar-değer -------------------------------------------------
    def kv_get(self, k: str) -> Any:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else None

    def kv_set(self, k: str, v: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))
