"""Turso HTTP adaptörü ve sunucusuz akış testleri (gerçek ağ yok)."""
import sqlite3
from datetime import date, timedelta

import pytest

from ucuzgez.libsql_http import LibsqlConnection


class FakeTurso:
    """Hrana v2 pipeline'ını gerçek bir in-memory SQLite ile taklit eder."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.headers = {}
        self.requests = 0

    @staticmethod
    def _py(a):
        t = a["type"]
        return None if t == "null" else int(a["value"]) if t == "integer" else \
            float(a["value"]) if t == "float" else a.get("value")

    @staticmethod
    def _enc(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        return {"type": "text", "value": str(v)}

    def post(self, url, json=None, timeout=None):
        assert url.endswith("/v2/pipeline") and self.headers["Authorization"] == "Bearer tok"
        self.requests += 1
        results = []
        for req in json["requests"]:
            if req["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            st = req["stmt"]
            try:
                cur = self.db.execute(st["sql"], [self._py(a) for a in st["args"]])
            except sqlite3.Error as e:
                results.append({"type": "error", "error": {"message": str(e)}})
                continue
            cols = [{"name": d[0]} for d in (cur.description or [])]
            rows = [[self._enc(v) for v in r] for r in cur.fetchall()]
            results.append({"type": "ok", "response": {"type": "execute", "result": {
                "cols": cols, "rows": rows, "affected_row_count": max(cur.rowcount, 0),
                "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None}}})
        self.db.commit()

        class R:
            def raise_for_status(self): pass
            def json(self_inner): return {"results": results}
        return R()


def test_libsql_adapter_basics():
    fake = FakeTurso()
    c = LibsqlConnection("libsql://x.turso.io", "tok", session=fake)
    c.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT, b REAL);\nCREATE TABLE u (x INT);\n")
    cur = c.execute("INSERT INTO t(a,b) VALUES(?,?)", ("ş", 1.5))
    assert cur.lastrowid == 1 and cur.rowcount == 1
    c.executemany("INSERT INTO t(a,b) VALUES(?,?)", [("x", 2.0), ("y", None)])
    rows = c.execute("SELECT * FROM t ORDER BY id").fetchall()
    assert [r["a"] for r in rows] == ["ş", "x", "y"] and rows[2]["b"] is None and rows[0][0] == 1
    assert c.execute("PRAGMA journal_mode=WAL").fetchone() is None
    with pytest.raises(RuntimeError):
        c.execute("SELECT * FROM yok")


def test_services_on_turso(monkeypatch, tmp_path):
    fake = FakeTurso()
    import ucuzgez.libsql_http as lh
    orig = lh.LibsqlConnection.__init__
    monkeypatch.setattr(lh.LibsqlConnection, "__init__",
                        lambda self, url, token, session=None: orig(self, url, token, session=fake))
    monkeypatch.setenv("UG_PROVIDER", "mock")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://db-org.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("UG_DB_PATH", str(tmp_path / "unused.db"))
    from ucuzgez.config import load_settings
    from ucuzgez.services import Services
    from ucuzgez.planner import TripQuery
    svc = Services(load_settings())
    assert not (tmp_path / "unused.db").exists()
    u = svc.db.add_user("Turso")
    q = TripQuery(["IST"], date.today() + timedelta(days=10), date.today() + timedelta(days=20), 3, 4)
    wid = svc.db.add_watch(u["id"], "w", svc.query_to_params(q), 10_000_000)
    msg = svc.check_watch(svc.db.one("SELECT * FROM watches WHERE id=?", (wid,)))
    assert msg and "Fiyat düştü" in msg
    assert svc.db.kv_get("yok") is None
    svc.db.kv_set("k", {"a": 1})
    assert svc.db.kv_get("k") == {"a": 1}
    assert svc.db.delete_watch(u["id"], wid)
