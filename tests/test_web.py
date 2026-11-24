import time
from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

from ucuzgez.config import load_settings
from ucuzgez.planner import TripQuery
from ucuzgez.services import Services
from ucuzgez.web import create_web_app, price_calendar, sparkline, tint


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("UG_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("UG_PROVIDER", "mock")
    monkeypatch.setenv("UG_CURRENCY", "try")
    svc = Services(load_settings())
    u = svc.db.add_user("Deneme")
    svc.db.set_home(u["id"], code="IST")
    c = TestClient(create_web_app(svc))
    c.cookies.set("ug_t", u["token"])
    return svc, u, c


def _range():
    a = date.today() + timedelta(days=1)
    return a.isoformat(), (a + timedelta(days=40)).isoformat()


def test_search_renders_calendar_and_actions(env):
    _, _, c = env
    a, b = _range()
    r = c.get(f"/ara?date_from={a}&date_to={b}&min_days=3&max_days=5&to=r:balkanlar")
    assert r.status_code == 200
    assert "Fiyat takvimi" in r.text and 'class="day l' in r.text
    assert 'data-act="ics"' in r.text and "Paylaş" in r.text
    assert "En ucuz çıkış günü" in r.text


def test_day_link_narrows_to_single_day(env):
    _, _, c = env
    a, b = _range()
    r = c.get(f"/ara?date_from={a}&date_to={b}&min_days=3&max_days=5&to=r:balkanlar")
    import re
    href = re.search(r'class="day l\d[^"]*" href="([^"]+)"', r.text).group(1).replace("&amp;", "&")
    day = re.search(r"date_from=([\d-]+)", href).group(1)
    r2 = c.get(href)
    assert r2.status_code == 200 and "yalnız" in r2.text and "Fiyat takvimi" not in r2.text
    assert f'data-start="{day}"' in r2.text


def test_watch_toggle_and_search_link(env):
    svc, u, c = env
    q = TripQuery(["IST"], date.today() + timedelta(days=2), date.today() + timedelta(days=30), 3, 5)
    p = svc.query_to_params(q, 60)
    p["_targets"] = {"include": ["r:balkanlar"], "exclude": ["c:BG"]}
    wid = svc.db.add_watch(u["id"], "Balkanlar", p, 4000)
    with svc.db.tx() as cur:
        for i, price in enumerate([5200, 4900, 5100, 4700]):
            cur.execute("INSERT INTO watch_history VALUES(?,?,?)", (wid, price, time.time() - (4 - i) * 3600))
    r = c.get("/takipler")
    assert "Şimdi ara" in r.text and "to=r%3Abalkanlar" in r.text and "not=c%3ABG" in r.text
    assert "<svg viewBox=\"0 0 144 40\"" in r.text and "4.700" in r.text
    assert c.post(f"/takipler/{wid}/durum", data={"active": "0"}, follow_redirects=False).status_code == 303
    assert svc.db.watches(u["id"])[0]["active"] == 0
    c.post(f"/takipler/{wid}/durum", data={"active": "1"})
    assert svc.db.watches(u["id"])[0]["active"] == 1


def test_watch_toggle_other_user_is_ignored(env):
    svc, u, c = env
    other = svc.db.add_user("Baska")
    q = TripQuery(["IST"], date.today() + timedelta(days=2), date.today() + timedelta(days=30), 3, 5)
    wid = svc.db.add_watch(other["id"], "X", svc.query_to_params(q), None)
    c.post(f"/takipler/{wid}/durum", data={"active": "0"})
    assert svc.db.watches(other["id"])[0]["active"] == 1


def test_pwa_files(env):
    _, _, c = env
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["display"] == "standalone"
    for path, kind in [("/icon.svg", "image/svg+xml"), ("/icon-180.png", "image/png"), ("/icon-512.png", "image/png")]:
        r = c.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind)


def test_pages_render(env):
    _, _, c = env
    for path in ["/", "/takipler", "/ayarlar"]:
        assert c.get(path).status_code == 200
    c.cookies.clear()
    assert "Giriş linki ya da kod" in c.get("/").text


def test_price_calendar_levels():
    d1 = date(2026, 12, 30)
    daily = {d1: (1000.0, "VIE"), d1 + timedelta(days=1): (2000.0, "PRG"), d1 + timedelta(days=2): (3000.0, "BUD")}
    months = price_calendar(daily, d1, d1 + timedelta(days=2))
    assert [m["label"] for m in months] == ["Aralık 2026", "Ocak 2027"]
    cells = [c for m in months for w in m["weeks"] for c in w if c and c["price"]]
    assert [c["lvl"] for c in cells] == [0, 2, 4] and cells[0]["best"]
    assert all(len(w) == 7 for m in months for w in m["weeks"])


def test_sparkline_and_tint():
    assert sparkline([(0, 100)]) is None
    s = sparkline([(0, 300), (10, 100), (20, 200)])
    assert s["line"].startswith("M") and s["down"] and s["lo"] == 100
    assert tint("VIE") == tint("VIE")


@pytest.mark.parametrize("form", ["{tok}", "  {tok}. ", "https://ticket-tracking-sage.vercel.app/giris?t={tok}",
                                  "{Tok}", "{inv}", "/start {inv}", "{INV}"])
def test_login_accepts_link_token_or_invite(env, form):
    svc, u, c = env
    c.cookies.clear()
    tok, inv = u["token"], u["tg_invite"]
    value = form.format(tok=tok, Tok=tok[:1].swapcase() + tok[1:], inv=inv, INV=inv.upper())
    r = c.post("/giris", data={"token": value}, follow_redirects=False)
    assert r.status_code == 303 and r.cookies.get("ug_t") == tok


def test_login_rejects_wrong_code(env):
    _, _, c = env
    c.cookies.clear()
    for bad in ["", "yanlis", "/start 00000000", "https://x/giris?t=yok"]:
        assert c.post("/giris", data={"token": bad}, follow_redirects=False).status_code == 401
