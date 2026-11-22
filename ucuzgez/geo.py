"""Havalimanı veritabanı ve konum hesapları."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SEED_FILE = DATA_DIR / "airports_seed.json"
FULL_FILE = DATA_DIR / "airports_full.json"   # cli update-airports ile oluşur


@dataclass(frozen=True)
class Airport:
    code: str
    city_code: str
    city: str
    country: str
    lat: float
    lon: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class AirportDB:
    def __init__(self, airports: list[Airport], popular: set[str] | None = None):
        # popüler şehirler: elle seçilmiş liste (airports_seed.json). Dönüş şehri seçiminde öncelikli.
        self.popular: set[str] = popular if popular is not None else {a.city_code for a in airports}
        self.by_code: dict[str, Airport] = {}
        self.by_city: dict[str, list[Airport]] = {}
        for a in airports:
            self.by_code[a.code] = a
            self.by_city.setdefault(a.city_code, []).append(a)
        # şehir merkezi ~ havalimanlarının ortalaması
        self.city_center: dict[str, tuple[float, float]] = {
            c: (sum(a.lat for a in lst) / len(lst), sum(a.lon for a in lst) / len(lst))
            for c, lst in self.by_city.items()
        }

    # ---- yükleme -------------------------------------------------------
    @classmethod
    def load(cls) -> "AirportDB":
        rows: dict[str, Airport] = {}
        popular: set[str] = set()
        for f in (FULL_FILE, SEED_FILE):          # seed en son -> Türkçe isimler kazanır
            if f.exists():
                for r in json.loads(f.read_text(encoding="utf-8"))["airports"]:
                    rows[r[0]] = Airport(r[0], r[1], r[2], r[3], float(r[4]), float(r[5]))
                    if f == SEED_FILE:
                        popular.add(r[1])
        return cls(list(rows.values()), popular)

    # ---- sorgular ------------------------------------------------------
    def city_of(self, code: str) -> str:
        code = code.upper()
        if code in self.by_city:
            return code
        a = self.by_code.get(code)
        return a.city_code if a else code

    def city_name(self, code: str) -> str:
        city = self.city_of(code)
        lst = self.by_city.get(city)
        return lst[0].city if lst else code

    def country_of(self, code: str) -> str | None:
        lst = self.by_city.get(self.city_of(code))
        return lst[0].country if lst else None

    def city_distance_km(self, a: str, b: str) -> float | None:
        ca, cb = self.city_center.get(self.city_of(a)), self.city_center.get(self.city_of(b))
        if not ca or not cb:
            return None
        return haversine_km(ca[0], ca[1], cb[0], cb[1])

    def cities_near_point(self, lat: float, lon: float, radius_km: float) -> list[tuple[str, float]]:
        """Bir noktanın çevresindeki şehirler (en yakın havalimanına göre), yakından uzağa."""
        best: dict[str, float] = {}
        for a in self.by_code.values():
            d = haversine_km(lat, lon, a.lat, a.lon)
            if d <= radius_km and d < best.get(a.city_code, 1e9):
                best[a.city_code] = d
        return sorted(best.items(), key=lambda x: x[1])

    def cities_near_city(self, city: str, max_km: float) -> list[tuple[str, float]]:
        city = self.city_of(city)
        c = self.city_center.get(city)
        if not c:
            return []
        out = []
        for other, oc in self.city_center.items():
            if other == city:
                continue
            d = haversine_km(c[0], c[1], oc[0], oc[1])
            if d <= max_km:
                out.append((other, d))
        return sorted(out, key=lambda x: x[1])


@lru_cache(maxsize=1)
def get_airports() -> AirportDB:
    return AirportDB.load()
