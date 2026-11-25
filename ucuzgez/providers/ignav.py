"""Ignav Flight Prices API (canlı fiyat, Amadeus Self-Service'in yerine geçen kaynak).

- https://ignav.com/docs, kimlik: `X-Api-Key`. İlk 1.000 istek ücretsiz, sonra 1.000 istek ~2 USD.
- `cheapest-dates`: bir rotada tarih aralığındaki günlük en ucuz fiyat (tek çağrı). Ay taraması için.
- `fares/one-way`: belirli günün canlı uçuşları; firma, uçuş no, saat. Doğrulama için.
- "Her yere" araması yoktur; o iş Travelpayouts'ta kalır.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta

import requests

from .base import Fare

log = logging.getLogger(__name__)
BASE = "https://ignav.com/api"


class _Ignav:
    def __init__(self, key: str, currency: str, eur_rate: float, airports, market: str = "TR", session=None):
        if not key:
            raise ValueError("IGNAV_API_KEY boş")
        self.key = key
        self.currency = currency.lower()
        self.eur_rate = eur_rate
        self.airports = airports
        self.market = market.upper()
        self.http = session or requests.Session()

    def _post(self, path: str, body: dict) -> dict | None:
        try:
            r = self.http.post(BASE + path, json={**body, "market": self.market}, timeout=45,
                               headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
            if r.status_code >= 400:
                log.warning("Ignav %s %s: %s", path, r.status_code, r.text[:200])
                return None
            return r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Ignav hatası %s: %s", path, e)
            return None

    def _price(self, p: dict | None) -> float | None:
        if not p or p.get("amount") is None:
            return None
        cur = (p.get("currency") or "").lower()
        amount = float(p["amount"])
        if cur == self.currency:
            return amount
        if cur == "eur":
            return round(amount * self.eur_rate, 2)
        return None                     # bilinmeyen kur: yanlış fiyat göstermektense atla

    def _airports(self, code: str) -> list[str]:
        """Şehir kodunu havalimanlarına açar (İstanbul -> IST, SAW). Havalimanı kodu olduğu gibi kalır."""
        city = self.airports.city_of(code)
        lst = [a.code for a in (self.airports.by_city.get(city) or [])]
        if code == city and len(lst) > 1:
            big = [c for c in lst if c in self.airports.major] or lst
            return big[:2]
        return [code]


class IgnavProvider(_Ignav):
    """Belirli rota için aylık günlük en ucuz fiyatlar (canlı)."""
    name = "ignav"

    def fetch_month(self, origin: str, destination: str | None, month: str) -> list[Fare]:
        if not destination:
            return []
        y, m = map(int, month.split("-"))
        start = max(date(y, m, 1), date.today() + timedelta(days=1))
        end = date(y, m, calendar.monthrange(y, m)[1])
        if start > end:
            return []
        out: list[Fare] = []
        for o in self._airports(origin):
            for d in self._airports(destination):
                js = self._post("/fares/cheapest-dates", {
                    "origin": o, "destination": d,
                    "departure_date_range": {"start": start.isoformat(), "end": end.isoformat()},
                })
                for it in (js or {}).get("dates") or []:
                    price = self._price(it.get("price")) if it.get("status") == "priced" else None
                    if price is None:
                        continue
                    try:
                        day = date.fromisoformat(it["departure_date"][:10])
                    except (KeyError, ValueError):
                        continue
                    out.append(Fare(origin=o, destination=d, origin_city=self.airports.city_of(o),
                                    destination_city=self.airports.city_of(d), depart=day, price=price,
                                    currency=self.currency, source="ignav"))
        return out


class IgnavVerifier(_Ignav):
    """Belirli günün canlı en ucuz tek yön uçuşu: firma, uçuş numarası, saat."""
    name = "ignav"

    def verify_oneway(self, origin: str, destination: str, day: date) -> Fare | None:
        best = None
        for o in self._airports(origin):
            for d in self._airports(destination):
                js = self._post("/fares/one-way", {"origin": o, "destination": d,
                                                    "departure_date": day.isoformat(), "max_stops": 1})
                for it in (js or {}).get("itineraries") or []:
                    price = self._price(it.get("price"))
                    if price is not None and (best is None or price < best[0]):
                        best = (price, it, o, d)
        if not best:
            return None
        price, it, o, d = best
        leg = it.get("outbound") or ((it.get("legs") or [{}])[0])
        segs = leg.get("segments") or [{}]
        first, last = segs[0], segs[-1]
        t = None
        try:
            t = datetime.fromisoformat(first["departure_time_local"]).strftime("%H:%M")
        except (KeyError, ValueError, TypeError):
            pass
        code = first.get("marketing_carrier_code")
        dep, arr = first.get("departure_airport") or o, last.get("arrival_airport") or d
        return Fare(origin=dep, destination=arr, origin_city=self.airports.city_of(dep),
                    destination_city=self.airports.city_of(arr), depart=day, depart_time=t, price=price,
                    currency=self.currency, source="ignav", airline=code,
                    flight_number=f"{code}{first.get('flight_number')}" if code and first.get("flight_number") else None,
                    transfers=max(0, len(segs) - 1), duration_min=leg.get("duration_minutes"), verified=True)
