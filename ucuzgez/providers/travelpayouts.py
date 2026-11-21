"""Travelpayouts (Aviasales) Data API sağlayıcısı.

- Ücretsiz; travelpayouts.com'a kaydolup Aviasales programına bağlanınca token verilir.
- Veriler ÖNBELLEKTİR: Aviasales kullanıcılarının son ~48 saatte bulduğu en ucuz fiyatlar.
  Keşif ve alarm için çok uygun; satın almadan önce fiyatı sitede mutlaka kontrol et
  (ya da SERPAPI_KEY / DUFFEL_TOKEN ile canlı doğrulamayı aç).
- Pegasus (PC), AJet (VF), THY (TK), Wizz, Ryanair vb. Aviasales'te görünen tüm firmalar gelir.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime

import requests

from .base import Fare

log = logging.getLogger(__name__)

URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
SITE = "https://www.aviasales.com"


class RateLimiter:
    def __init__(self, per_min: int):
        self.per_min = max(1, per_min)
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.per_min:
                time.sleep(60 - (now - self.calls[0]) + 0.05)
            self.calls.append(time.monotonic())


class TravelpayoutsProvider:
    name = "tp"

    def __init__(self, token: str, currency: str = "try", market: str = "tr",
                 rate_per_min: int = 50, session: requests.Session | None = None, city_of=None):
        if not token:
            raise ValueError("TRAVELPAYOUTS_TOKEN boş")
        self.token = token
        self.currency = currency
        self.market = market
        self.limiter = RateLimiter(rate_per_min)
        self.http = session or requests.Session()
        self.city_of = city_of or (lambda c: c)

    def _get(self, params: dict) -> dict:
        for attempt in range(3):
            self.limiter.wait()
            try:
                r = self.http.get(URL, params=params, headers={"X-Access-Token": self.token}, timeout=20)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                log.warning("Travelpayouts hatası (%s): %s", attempt + 1, e)
                time.sleep(2 * (attempt + 1))
        return {"success": False, "data": []}

    def fetch_month(self, origin: str, destination: str | None, month: str) -> list[Fare]:
        fares: list[Fare] = []
        page = 1
        while page <= 3:   # her sayfa en fazla 1000 kayıt
            params = {
                "origin": origin, "departure_at": month, "one_way": "true",
                "direct": "false", "sorting": "price", "unique": "false",
                "currency": self.currency, "market": self.market,
                "limit": 1000, "page": page,
            }
            if destination:
                params["destination"] = destination
            data = self._get(params)
            rows = data.get("data") or []
            for it in rows:
                f = self._parse(it)
                if f:
                    fares.append(f)
            if len(rows) < 1000:
                break
            page += 1
        return fares

    def _parse(self, it: dict) -> Fare | None:
        try:
            dt = datetime.fromisoformat(it["departure_at"])
        except (KeyError, ValueError):
            return None
        o = it.get("origin_airport") or it.get("origin")
        d = it.get("destination_airport") or it.get("destination")
        return Fare(
            origin=o, destination=d,
            origin_city=it.get("origin") or self.city_of(o),
            destination_city=it.get("destination") or self.city_of(d),
            depart=dt.date(), depart_time=dt.strftime("%H:%M"),
            price=float(it["price"]), currency=self.currency, source="travelpayouts",
            airline=it.get("airline"), flight_number=str(it.get("flight_number") or "") or None,
            transfers=int(it.get("transfers") or 0), duration_min=it.get("duration_to") or it.get("duration"),
            link=(SITE + it["link"]) if it.get("link") else None,
        )
