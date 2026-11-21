"""Canlı fiyat doğrulayıcılar (isteğe bağlı, ücretli olabilir).

Planlayıcı önce ucuz/önbellekli kaynakla (Travelpayouts) geniş tarama yapar,
sonra yalnızca en iyi N sonucu burada CANLI fiyatla kontrol eder. Böylece
ücretli API çağrısı sayısı çok düşük kalır.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import requests

from .base import Fare

log = logging.getLogger(__name__)


class SerpApiGoogleFlights:
    """Google Flights sonuçları (SerpApi üzerinden). Pegasus/AJet dahil Google'da görünen her şey."""
    name = "serpapi"

    def __init__(self, api_key: str, currency: str, airports, session=None):
        self.key = api_key
        self.currency = currency.upper()
        self.airports = airports
        self.http = session or requests.Session()

    def _ids(self, code: str) -> str:
        city = self.airports.city_of(code)
        lst = self.airports.by_city.get(city)
        return ",".join(a.code for a in lst) if lst else code

    def verify_oneway(self, origin: str, destination: str, day: date) -> Fare | None:
        params = {
            "engine": "google_flights", "type": "2", "hl": "tr", "gl": "tr",
            "departure_id": self._ids(origin), "arrival_id": self._ids(destination),
            "outbound_date": day.isoformat(), "currency": self.currency, "api_key": self.key,
        }
        try:
            r = self.http.get("https://serpapi.com/search.json", params=params, timeout=60)
            r.raise_for_status()
            js = r.json()
        except requests.RequestException as e:
            log.warning("SerpApi hatası: %s", e)
            return None
        options = (js.get("best_flights") or []) + (js.get("other_flights") or [])
        options = [o for o in options if o.get("price")]
        if not options:
            return None
        best = min(options, key=lambda o: o["price"])
        legs = best.get("flights") or [{}]
        first = legs[0]
        dep = (first.get("departure_airport") or {})
        arr = (legs[-1].get("arrival_airport") or {})
        t = (dep.get("time") or "")[-5:] or None
        fn = first.get("flight_number") or ""
        return Fare(
            origin=dep.get("id", origin), destination=arr.get("id", destination),
            origin_city=self.airports.city_of(dep.get("id", origin)),
            destination_city=self.airports.city_of(arr.get("id", destination)),
            depart=day, depart_time=t, price=float(best["price"]), currency=self.currency.lower(),
            source="google_flights", airline=fn.split(" ")[0] if fn else None, flight_number=fn or None,
            transfers=max(0, len(legs) - 1), duration_min=best.get("total_duration"),
            link=(js.get("search_metadata") or {}).get("google_flights_url"), verified=True,
        )


class DuffelVerifier:
    """Duffel (NDC + GDS). Pegasus Duffel'da Travelport üzerinden mevcut."""
    name = "duffel"

    def __init__(self, token: str, currency: str, eur_rate: float, airports, session=None):
        self.token = token
        self.currency = currency.upper()
        self.eur_rate = eur_rate
        self.airports = airports
        self.http = session or requests.Session()

    def _convert(self, amount: float, cur: str) -> float | None:
        cur = cur.upper()
        if cur == self.currency:
            return amount
        if cur == "EUR" and self.eur_rate > 0:
            return amount * self.eur_rate
        return None

    def verify_oneway(self, origin: str, destination: str, day: date) -> Fare | None:
        body = {"data": {
            "slices": [{"origin": origin, "destination": destination, "departure_date": day.isoformat()}],
            "passengers": [{"type": "adult"}], "cabin_class": "economy", "max_connections": 1,
        }}
        try:
            r = self.http.post(
                "https://api.duffel.com/air/offer_requests?return_offers=true&supplier_timeout=15000",
                json=body, timeout=60,
                headers={"Authorization": f"Bearer {self.token}", "Duffel-Version": "v2",
                         "Accept": "application/json", "Content-Type": "application/json"})
            r.raise_for_status()
            offers = (r.json().get("data") or {}).get("offers") or []
        except requests.RequestException as e:
            log.warning("Duffel hatası: %s", e)
            return None
        best, best_price = None, None
        for o in offers:
            p = self._convert(float(o["total_amount"]), o["total_currency"])
            if p is not None and (best_price is None or p < best_price):
                best, best_price = o, p
        if not best:
            return None
        segs = best["slices"][0]["segments"]
        s0 = segs[0]
        dep = datetime.fromisoformat(s0["departing_at"])
        carrier = (s0.get("marketing_carrier") or {}).get("iata_code")
        return Fare(
            origin=s0["origin"]["iata_code"], destination=segs[-1]["destination"]["iata_code"],
            origin_city=self.airports.city_of(s0["origin"]["iata_code"]),
            destination_city=self.airports.city_of(segs[-1]["destination"]["iata_code"]),
            depart=dep.date(), depart_time=dep.strftime("%H:%M"), price=round(best_price, 2),
            currency=self.currency.lower(), source="duffel", airline=carrier,
            flight_number=f"{carrier}{s0.get('marketing_carrier_flight_number', '')}",
            transfers=len(segs) - 1, verified=True,
        )
