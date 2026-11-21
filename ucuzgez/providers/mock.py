"""Sahte (deterministik) veri sağlayıcı — API anahtarı olmadan sistemi denemek için.

Fiyatlar mesafe + gün + rota bazlı sabit 'gürültü' ile üretilir; aynı girdi hep
aynı fiyatı verir. Açık çene örneği için bilerek Viyana->İstanbul pahalı,
Prag->İstanbul ucuz yapılmıştır.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

from ..geo import AirportDB
from .base import Fare

AIRLINES = ["PC", "VF", "TK", "W6", "FR", "U2"]
# (kalkış şehri, varış şehri): çarpan  — demo için
ROUTE_BIAS = {("VIE", "IST"): 1.9, ("PRG", "IST"): 0.55, ("BUD", "IST"): 0.7,
              ("IST", "VIE"): 0.7, ("BTS", "IST"): 0.8}


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class MockProvider:
    name = "mock"

    def __init__(self, airports: AirportDB, currency: str = "try", eur_rate: float = 54.0):
        self.airports = airports
        self.currency = currency
        self.factor = 1.0 if currency == "try" else (1.0 / eur_rate if eur_rate else 1.0)

    def _price(self, o: str, d: str, day: date) -> float | None:
        km = self.airports.city_distance_km(o, d)
        if km is None or km < 150:
            return None
        if _h(o, d, day) % 7 == 0:          # o gün uçuş yok
            return None
        base = 900 + km * 1.4
        weekday = {4: 1.25, 6: 1.2, 1: 0.85, 2: 0.85}.get(day.weekday(), 1.0)
        noise = 0.6 + (_h("n", o, d, day) % 90) / 100   # 0.6 - 1.5
        p = base * weekday * noise * ROUTE_BIAS.get((o, d), 1.0)
        return round(p * self.factor, 0)

    def fetch_month(self, origin: str, destination: str | None, month: str) -> list[Fare]:
        o = self.airports.city_of(origin)
        y, m = map(int, month.split("-"))
        day = date(y, m, 1)
        dests = [self.airports.city_of(destination)] if destination else list(self.airports.by_city)
        out = []
        while day.month == m:
            for d in dests:
                if d == o:
                    continue
                p = self._price(o, d, day)
                if p is None:
                    continue
                al = AIRLINES[_h("a", o, d, day) % len(AIRLINES)]
                oa = self.airports.by_city[o][0].code if o in self.airports.by_city else o
                da = self.airports.by_city[d][0].code if d in self.airports.by_city else d
                out.append(Fare(
                    origin=oa, destination=da, origin_city=o, destination_city=d, depart=day,
                    depart_time=f"{6 + _h('t', o, d, day) % 16:02d}:{(_h('m', o, d, day) % 4) * 15:02d}",
                    price=p, currency=self.currency, source="mock", airline=al,
                    flight_number=f"{al}{100 + _h('f', o, d) % 900}",
                    transfers=0 if _h("x", o, d) % 4 else 1,
                ))
            day += timedelta(days=1)
        return out
