"""Metin komutlarını ayrıştırma ve işleme (Telegram'dan bağımsız, test edilebilir)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .planner import TripQuery

AIRLINE_WORDS = {"pegasus": "PC", "ajet": "VF", "anadolujet": "VF", "thy": "TK", "wizz": "W6",
                 "ryanair": "FR", "easyjet": "U2", "sunexpress": "XQ"}

HELP = """<b>UcuzGez komutları</b>

<b>/ara</b> [kalkış] [tarih1] [tarih2] [gün] [bütçe] [hedef...] [seçenekler]
  /ara IST 01.12 20.12 3-5
  /ara konum 05.12.2026 15.12.2026 4 3000
  /ara IST 01.12 31.12 3-7 VIE PRG BUD
  /ara SAW 01.12 20.12 2-4 direkt pegasus ajet
  Tarih vermezsen: yarından itibaren 60 gün. Gün: "4" ya da "3-6".
  Seçenekler: direkt · aynisehir (farklı şehirden dönüş önerme) · yurtdisi · pegasus/ajet/thy...

<b>/kesfet</b> [gün] — konumundan önümüzdeki 60 günde en ucuz geziler (örn /kesfet 3-5)
<b>/takip</b> … — /ara ile aynı + bütçe = hedef fiyat. Sistem düzenli kontrol eder, düşünce yazar.
  /takip IST 3-5 4000   (tarihsiz = sürekli önümüzdeki 60 gün)
<b>/takipler</b> · <b>/sil</b> 3
<b>/konum</b> — konumunu paylaş (yakın havalimanlarından arar) · <b>/ev</b> IST · <b>/yaricap</b> 200
<b>/radar</b> — fırsat radarını şimdi çalıştır (normalde her sabah otomatik)
<b>/davet</b> İsim · <b>/kullanicilar</b> — (yönetici) arkadaş davet et / kullanıcıları gör
"""

_DATE_RES = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{1,2})[./](\d{1,2})$"), ("d", "m")),
]


def parse_date(tok: str, today: date | None = None) -> date | None:
    today = today or date.today()
    for rx, order in _DATE_RES:
        m = rx.match(tok)
        if not m:
            continue
        parts = dict(zip(order, map(int, m.groups())))
        try:
            if "y" in parts:
                return date(parts["y"], parts["m"], parts["d"])
            d = date(today.year, parts["m"], parts["d"])
            return d if d >= today else date(today.year + 1, parts["m"], parts["d"])
        except ValueError:
            raise ValueError(f"Geçersiz tarih: {tok}")
    return None


@dataclass
class ParsedSearch:
    origin_text: str | None
    query: TripQuery
    rolling_days: int | None
    budget: float | None


def parse_search(args: list[str], default_days: tuple[int, int] = (3, 7),
                 today: date | None = None) -> ParsedSearch:
    today = today or date.today()
    codes: list[str] = []
    dates: list[date] = []
    days: tuple[int, int] | None = None
    budget = None
    max_transfers, open_jaw = 1, True
    airlines: set[str] = set()
    excl_countries: set[str] = set()
    origin_text = None

    for raw in args:
        tok = raw.strip().strip(",")
        low = tok.lower()
        if not tok:
            continue
        d = parse_date(tok, today)
        if d:
            dates.append(d)
        elif re.fullmatch(r"\d{1,3}(\.\d{3})+", tok):        # 4.000 -> 4000 (binlik ayraç)
            budget = float(tok.replace(".", ""))
        elif re.fullmatch(r"\d{1,2}-\d{1,2}", tok):
            a, b = map(int, tok.split("-"))
            days = (min(a, b), max(a, b))
        elif re.fullmatch(r"\d+([.,]\d+)?", tok):
            v = float(tok.replace(",", "."))
            if v <= 30 and days is None:
                days = (int(v), int(v))
            else:
                budget = v
        elif low in ("direkt", "aktarmasiz", "aktarmasız"):
            max_transfers = 0
        elif low in ("aynisehir", "aynışehir", "kapali"):
            open_jaw = False
        elif low in ("yurtdisi", "yurtdışı"):
            excl_countries.add("TR")
        elif low in AIRLINE_WORDS:
            airlines.add(AIRLINE_WORDS[low])
        elif low in ("konum", "ev") and origin_text is None:
            origin_text = "konum"
        elif re.fullmatch(r"[A-Za-z]{3}(,[A-Za-z]{3})*", tok):
            codes.extend(c.upper() for c in tok.split(","))
        else:
            raise ValueError(f"Anlayamadım: '{tok}'. /yardim yaz.")

    if origin_text is None and codes:
        origin_text = codes.pop(0)
    destinations = codes or None

    rolling = None
    if not dates:
        d1, rolling = today + timedelta(days=1), 60
        d2 = d1 + timedelta(days=rolling)
    elif len(dates) == 1:
        d1 = d2 = dates[0]
    else:
        d1, d2 = min(dates[:2]), max(dates[:2])
    mn, mx = days or default_days

    q = TripQuery(origins=["?"], date_from=d1, date_to=d2, min_days=mn, max_days=mx,
                  destinations=destinations, budget=budget, open_jaw=open_jaw,
                  max_transfers=max_transfers, airlines=airlines or None,
                  exclude_countries=excl_countries or None)
    return ParsedSearch(origin_text, q, rolling, budget)
