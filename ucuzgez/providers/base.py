"""Sağlayıcı arayüzü ve ortak tipler.

Her veri kaynağı (Travelpayouts, mock, ileride başka bir API) yalnızca
`fetch_month(origin, destination|None, "YYYY-MM")` uygular. Tarih aralığına
bölme, filtreleme ve önbellek bu dosyadaki `FareSearch` tarafından yapılır.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass
class Fare:
    origin: str                 # havalimanı veya şehir kodu
    destination: str
    origin_city: str
    destination_city: str
    depart: date
    price: float
    currency: str
    source: str
    depart_time: str | None = None      # "HH:MM"
    airline: str | None = None          # IATA kodu (PC=Pegasus, VF=AJet, TK=THY...)
    flight_number: str | None = None
    transfers: int = 0
    duration_min: int | None = None
    link: str | None = None
    verified: bool = False              # canlı fiyatla doğrulandı mı
    fetched_at: float = field(default_factory=time.time)

    def to_json(self) -> dict:
        d = asdict(self)
        d["depart"] = self.depart.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Fare":
        d = dict(d)
        d["depart"] = date.fromisoformat(d["depart"])
        return cls(**d)


class FareProvider(Protocol):
    name: str

    def fetch_month(self, origin: str, destination: str | None, month: str) -> list[Fare]:
        """Bir ay içindeki tek yön en ucuz uçuşlar. destination=None -> her yere."""
        ...


class Verifier(Protocol):
    name: str

    def verify_oneway(self, origin: str, destination: str, day: date) -> Fare | None:
        """Belirli gün için CANLI en ucuz tek yön fiyat."""
        ...


class Cache(Protocol):
    def cache_get(self, key: str, max_age_s: float) -> str | None: ...
    def cache_set(self, key: str, value: str) -> None: ...


def months_between(d1: date, d2: date) -> list[str]:
    out, y, m = [], d1.year, d1.month
    while (y, m) <= (d2.year, d2.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


class FareSearch:
    """Sağlayıcı + önbellek + filtre. Planlayıcı yalnızca bunu kullanır."""

    def __init__(self, provider: FareProvider, cache: Cache | None = None, ttl_min: int = 360):
        self.provider = provider
        self.cache = cache
        self.ttl_s = ttl_min * 60
        self.api_calls = 0
        self.cache_hits = 0

    def _month(self, origin: str, destination: str | None, month: str) -> list[Fare]:
        key = f"{self.provider.name}|{origin}|{destination or '*'}|{month}"
        if self.cache is not None:
            hit = self.cache.cache_get(key, self.ttl_s)
            if hit is not None:
                self.cache_hits += 1
                return [Fare.from_json(x) for x in json.loads(hit)]
        self.api_calls += 1
        fares = self.provider.fetch_month(origin, destination, month)
        if self.cache is not None:
            self.cache.cache_set(key, json.dumps([f.to_json() for f in fares]))
        return fares

    def search_oneway(self, origin: str, destination: str | None, date_from: date, date_to: date,
                      max_transfers: int = 1, airlines: set[str] | None = None,
                      exclude_airlines: set[str] | None = None) -> list[Fare]:
        today = datetime.now().date()
        out = []
        for month in months_between(date_from, date_to):
            for f in self._month(origin, destination, month):
                if not (date_from <= f.depart <= date_to) or f.depart < today:
                    continue
                if f.transfers > max_transfers:
                    continue
                if airlines and (f.airline or "") not in airlines:
                    continue
                if exclude_airlines and (f.airline or "") in exclude_airlines:
                    continue
                out.append(f)
        return out
