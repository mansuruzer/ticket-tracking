import json
from datetime import date, datetime, timedelta

import pytest

from ucuzgez.config import load_settings
from ucuzgez.services import Services
from ucuzgez.scheduler import Scheduler
from ucuzgez.telegram_bot import TelegramBot, split_message


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("UG_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("UG_PROVIDER", "mock")
    monkeypatch.setenv("UG_CURRENCY", "try")
    return Services(load_settings())


@pytest.fixture
def bot(svc):
    b = TelegramBot("x", svc)
    b.sent = []
    b.send = lambda chat, text, reply_markup=None: b.sent.append((chat, text))
    return b


def msg(text=None, chat=42, **extra):
    m = {"chat": {"id": chat}, **extra}
    if text is not None:
        m["text"] = text
    return m


def test_rolling_params_roundtrip(svc):
    from ucuzgez.planner import TripQuery
    q = TripQuery(["IST"], date.today() + timedelta(days=5), date.today() + timedelta(days=65), 3, 5,
                  airlines={"PC", "VF"})
    p = json.loads(json.dumps(svc.query_to_params(q, rolling_days=60)))
    q2 = svc.params_to_query(p)
    assert q2.date_from == q.date_from and q2.airlines == {"PC", "VF"}


def text_of(r):
    return r[0] if isinstance(r, tuple) else r


def test_telegram_flow(svc, bot):
    u = svc.db.add_user("Ali")
    assert "davet" in bot.handle(msg("/ara IST")).lower()
    assert "bağlandı" in bot.handle(msg(f"/start {u['tg_invite']}"))
    # konum paylaşımı -> İstanbul
    r = bot.handle(msg(location={"latitude": 41.0, "longitude": 29.0}))
    assert "İstanbul" in text_of(r)
    assert svc.home_origins(svc.db.user(u["id"])) == ["IST"]
    # /kesfet bölgesiz -> düğmeler
    r = bot.handle(msg("/kesfet 3-5"))
    assert isinstance(r, tuple) and "inline_keyboard" in r[1]
    datas = [b["callback_data"] for row in r[1]["inline_keyboard"] for b in row]
    assert "k:vizesiz:3-5" in datas
    # düğmeye basılması -> arama sonucu + takip düğmesi
    out = bot.handle_callback(42, "k:vizesiz:3-5")
    assert isinstance(out, tuple) and "₺" in out[0] and "1." in out[0]
    assert "Bu aramayı takip et" in str(out[1])
    # sonucu tek dokunuşla takibe al
    t = bot.handle_callback(42, "t:last")
    assert "Takip #1" in t and "Vizesiz" in t
    # bölgeli metin araması
    d = (date.today() + timedelta(days=10)).strftime("%d.%m.%Y")
    d2 = (date.today() + timedelta(days=30)).strftime("%d.%m.%Y")
    out = text_of(bot.handle(msg(f"/ara IST {d} {d2} 3-5 schengen")))
    assert "₺" in out
    r = bot.handle(msg("/takip IST 3-5 999999"))
    assert "Takip #2" in r
    assert "#1" in bot.handle(msg("/takipler"))
    assert bot.handle(msg("/sil 1")) == "Takip silindi."
    with pytest.raises(ValueError):
        bot.handle(msg("/ara IST ???"))
    # hiçbir yanıtta emoji olmasın
    import re
    for out in bot.sent:
        assert not re.search("[\U0001F300-\U0001FAFF\u2600-\u27BF]", out[1])


def test_watch_notifies_once(svc):
    u = svc.db.add_user("Ayşe")
    from ucuzgez.planner import TripQuery
    q = TripQuery(["IST"], date.today() + timedelta(days=10), date.today() + timedelta(days=25), 3, 5)
    wid = svc.db.add_watch(u["id"], "test", svc.query_to_params(q), threshold=10_000_000)
    w = svc.db.one("SELECT * FROM watches WHERE id=?", (wid,))
    first = svc.check_watch(w)
    assert first and "hedefin" in first and "Fiyat düştü" in first
    w = svc.db.one("SELECT * FROM watches WHERE id=?", (wid,))
    assert svc.check_watch(w) is None             # aynı fiyat -> tekrar bildirme
    assert svc.db.watch_median(wid)[1] == 2


def test_radar_detects_cheap_route(svc):
    u = svc.db.add_user("Can")
    svc.db.set_home(u["id"], code="IST")
    u = svc.db.user(u["id"])
    # ilk çalıştırma: geçmiş yok -> fırsat yok, ama fiyatlar kaydedilir
    assert svc.radar(u) is None
    # geçmişi yapay olarak pahalı göster: tüm kayıtları 3x yap, 6 farklı güne yay
    rows = svc.db.q("SELECT route, dep_date, price FROM price_history")
    with svc.db.tx() as c:
        c.execute("DELETE FROM price_history")
        for i in range(6):
            c.executemany("INSERT INTO price_history VALUES(?,?,?,?)",
                          [(r["route"], r["dep_date"], r["price"] * 3,
                            datetime.now().timestamp() - (i + 1) * 86400) for r in rows])
    out = svc.radar(u)
    assert out and "Fırsat radarı" in out
    assert svc.radar(u) is None or "Fırsat" in svc.radar(u)   # tekrar bildirim filtrelenir


def test_scheduler_tick_runs_watch_and_radar(svc):
    u = svc.db.add_user("Deniz")
    svc.db.set_home(u["id"], code="IST")
    with svc.db.tx() as c:
        c.execute("UPDATE users SET tg_chat_id=7 WHERE id=?", (u["id"],))
    from ucuzgez.planner import TripQuery
    q = TripQuery(["IST"], date.today() + timedelta(days=10), date.today() + timedelta(days=20), 3, 4)
    svc.db.add_watch(u["id"], "w", svc.query_to_params(q), threshold=10_000_000)
    sent = []
    sch = Scheduler(svc, lambda user, text: sent.append(text))
    sch.tick(now=datetime.now().replace(hour=23))
    assert any("Fiyat düştü" in s for s in sent)
    assert svc.db.kv_get("radar_day") == date.today().isoformat()


def test_split_message():
    parts = split_message("\n\n".join(["x" * 1500] * 5), limit=4000)
    assert all(len(p) <= 4000 for p in parts) and len(parts) >= 2


def test_admin_invite_and_single_use(svc, bot):
    admin = svc.db.add_user("Admin", is_admin=True)
    bot.handle(msg(f"/start {admin['tg_invite']}", chat=1))
    out = bot.handle(msg("/davet Ali", chat=1))
    code = out.split("/start ")[1].split("<")[0]
    assert "Ali" in out and "bağlandı" in bot.handle(msg(f"/start {code}", chat=2))
    assert "kullanılmış" in bot.handle(msg(f"/start {code}", chat=3))      # başkası kullanamaz
    assert "yönetici" in bot.handle(msg("/davet Veli", chat=2)).lower()     # admin olmayan
    assert "Ali" in bot.handle(msg("/kullanicilar", chat=1))
