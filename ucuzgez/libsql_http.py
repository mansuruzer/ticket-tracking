"""Turso / libSQL için sqlite3 benzeri minik HTTP istemcisi (ek bağımlılık yok).

Sunucusuz (Vercel) kurulumda yerel disk kalıcı olmadığı için veritabanı Turso'da
tutulur. Buradaki sınıflar `db.py`'nin kullandığı sqlite3 alt kümesini taklit eder:
execute / executemany / executescript / fetchone / fetchall / rowcount / lastrowid.
Her çağrı tek HTTP isteğidir (otomatik commit); commit/rollback etkisizdir.
"""
from __future__ import annotations

import base64
import re

import requests


class Row(dict):
    """sqlite3.Row gibi: r["kolon"] ve r[0] ile erişim."""

    def __init__(self, cols: list[str], vals: list):
        super().__init__(zip(cols, vals))
        self._vals = vals

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        return super().__getitem__(k)

    def keys(self):  # sqlite3.Row uyumu
        return list(super().keys())


def _arg(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(v).decode()}
    return {"type": "text", "value": str(v)}


def _val(c: dict):
    t = c.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(c["value"])
    if t == "float":
        return float(c["value"])
    if t == "blob":
        return base64.b64decode(c.get("base64", ""))
    return c.get("value")


class Cursor:
    def __init__(self, result: dict | None):
        result = result or {}
        cols = [c.get("name") or "" for c in result.get("cols", [])]
        self._rows = [Row(cols, [_val(x) for x in r]) for r in result.get("rows", [])]
        self.rowcount = result.get("affected_row_count", 0)
        lid = result.get("last_insert_rowid")
        self.lastrowid = int(lid) if lid is not None else None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class LibsqlConnection:
    def __init__(self, url: str, token: str, session: requests.Session | None = None):
        url = url.replace("libsql://", "https://").rstrip("/")
        self.endpoint = f"{url}/v2/pipeline"
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _pipeline(self, stmts: list[tuple[str, tuple]]) -> list[dict]:
        reqs = [{"type": "execute", "stmt": {"sql": s, "args": [_arg(a) for a in args]}} for s, args in stmts]
        reqs.append({"type": "close"})
        r = self.http.post(self.endpoint, json={"requests": reqs}, timeout=30)
        r.raise_for_status()
        out = []
        for res in r.json().get("results", [])[:-1]:
            if res.get("type") == "error":
                raise RuntimeError(f"libSQL hatası: {res.get('error', {}).get('message')}")
            out.append(res["response"]["result"])
        return out

    # ---- sqlite3 arayüzü -------------------------------------------------------
    def execute(self, sql: str, args: tuple = ()) -> Cursor:
        if sql.strip().upper().startswith("PRAGMA"):
            return Cursor(None)          # Turso'da gereksiz
        return Cursor(self._pipeline([(sql, tuple(args))])[0])

    def executemany(self, sql: str, seq) -> Cursor:
        stmts = [(sql, tuple(a)) for a in seq]
        if not stmts:
            return Cursor(None)
        res = self._pipeline(stmts)
        c = Cursor(None)
        c.rowcount = sum(x.get("affected_row_count", 0) for x in res)
        return c

    def executescript(self, script: str) -> None:
        stmts = [s.strip() for s in re.split(r";\s*\n", script) if s.strip()]
        self._pipeline([(s.rstrip(";"), ()) for s in stmts])

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass
