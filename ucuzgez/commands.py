"""Metin komutlarını ayrıştırma ve işleme (Telegram'dan bağımsız, test edilebilir)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .planner import TripQuery
from .regions import REGIONS, Region, find_region

AIRLINE_WORDS = {"pegasus": "PC", "ajet": "VF", "anadolujet": "VF", "thy": "TK", "wizz": "W6",
                 "ryanair": "FR", "easyjet": "U2", "sunexpress": "XQ"}

HELP = """<b>Komutlar</b>

<b>/kesfet</b> konumundan önümüzdeki 60 günde en ucuz geziler. Bölgeyi düğmeyle seçersin.
  /kesfet 3-5   (3 ile 5 gün arası)

<b>/ara</b> kalkış, tarihler, gün, bütçe, bölge ya da şehirler
  /ara IST 01.12 20.12 3-5
  /ara IST 3-6 vizesiz 4000
  /ara SAW 01.12 31.12 2-4 VIE PRG direkt pegasus
  Tarih yazmazsan yarından itibaren 60 gün. Bölgeler için /bolgeler.

<b>/takip</b> /ara ile aynı, sondaki sayı hedef fiyat. Düzenli bakarım, düşünce yazarım.
  /takip IST 3-5 schengen 6000
<b>/takipler</b> takiplerini gör, <b>/sil</b> 3 sil

<b>/konum</b> konumunu paylaş, <b>/ev</b> IST ev havalimanı, <b>/yaricap</b> 200 arama yarıçapı
<b>/radar</b> normalinden çok ucuz uçuşlara şimdi bak (her sabah kendiliğinden çalışır)
<b>/davet</b> İsim, <b>/kullanicilar</b> (sadece yönetici)
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
    include: list[str] = None      # belirteçler: r:schengen, c:BG, y:VIE
    exclude: list[str] = None


def build_query_targets(q: TripQuery, include: list[str], exclude: list[str]) -> TripQuery:
    """Dahil/hariç belirteçlerini sorguya uygular (web ve Telegram ortak)."""
    from .geo import get_airports
    from .places import Places
    sel = Places(get_airports()).resolve(include, exclude)
    q.include_countries = sel.include_countries
    q.include_cities = sel.include_cities
    q.exclude_countries = sel.exclude_countries or None
    q.exclude_cities = sel.exclude_cities or None
    q.destinations = sorted(sel.include_cities) if sel.only_cities else None
    regs = [t[2:] for t in include if t.startswith("r:")]
    q.region = regs[0] if regs else None
    return q


def parse_search(args: list[str], default_days: tuple[int, int] = (3, 7),
                 today: date | None = None) -> ParsedSearch:
    """Serbest metin arama. Hedefler: bölge adı (schengen), ülke kodu (BG), şehir kodu (VIE).
    Hariç tutmak için başına - koy (-BG, -BUH) ya da 'hariç' kelimesinden sonra yaz."""
    today = today or date.today()
    codes: list[str] = []
    include: list[str] = []
    exclude: list[str] = []
    excluding = False
    dates: list[date] = []
    days: tuple[int, int] | None = None
    budget = None
    max_transfers, open_jaw = 1, True
    airlines: set[str] = set()
    origin_text = None

    for raw in args:
        tok = raw.strip().strip(",")
        low = tok.lower()
        if not tok:
            continue
        neg = excluding
        if tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            neg, tok, low = True, tok[1:], low[1:]
        target = exclude if neg else include
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
        elif low in ("haric", "hariç", "harici", "disinda", "dışında"):
            excluding = True
        elif low in ("direkt", "aktarmasiz", "aktarmasız"):
            max_transfers = 0
        elif low in ("aynisehir", "aynışehir", "kapali"):
            open_jaw = False
        elif len(tok) > 3 and find_region(tok):
            target.append(f"r:{find_region(tok).key}")
        elif low in AIRLINE_WORDS:
            airlines.add(AIRLINE_WORDS[low])
        elif low in ("konum", "ev") and origin_text is None:
            origin_text = "konum"
        elif re.fullmatch(r"[A-Za-z]{2}", tok):
            target.append(f"c:{tok.upper()}")
        elif re.fullmatch(r"[A-Za-z]{3}(,[A-Za-z]{3})*", tok):
            if neg:
                exclude.extend(f"y:{c.upper()}" for c in tok.split(","))
            else:
                codes.extend(c.upper() for c in tok.split(","))
        else:
            raise ValueError(f"'{tok}' kısmını anlayamadım")

    if origin_text is None and codes:
        origin_text = codes.pop(0)
    include += [f"y:{c}" for c in codes]

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
                  budget=budget, open_jaw=open_jaw, max_transfers=max_transfers, airlines=airlines or None)
    build_query_targets(q, include, exclude)
    return ParsedSearch(origin_text, q, rolling, budget, include, exclude)
