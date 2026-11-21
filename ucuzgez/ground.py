"""Şehirler arası kara yolu (otobüs/tren) süre ve ücret TAHMİNİ.

Gerçek fiyat API'si yerine iki katman:
1) Bilinen popüler hatlar için elle girilmiş tipik değerler (OVERRIDES)
2) Geri kalanı için mesafe tabanlı formül.
Sonuçlar her zaman 'tahmini' olarak gösterilir.
"""
from __future__ import annotations

from dataclasses import dataclass

from .geo import AirportDB

# (şehir1, şehir2): (saat, tipik EUR) — FlixBus/tren gibi ucuz seçenekler baz alındı
OVERRIDES: dict[frozenset, tuple[float, float]] = {
    frozenset(("VIE", "BTS")): (1.0, 8),
    frozenset(("VIE", "BUD")): (2.7, 15),
    frozenset(("VIE", "PRG")): (4.0, 20),
    frozenset(("BTS", "BUD")): (2.5, 12),
    frozenset(("PRG", "BER")): (4.5, 25),
    frozenset(("PRG", "MUC")): (4.5, 25),
    frozenset(("MUC", "NUE")): (1.5, 15),
    frozenset(("MUC", "STR")): (2.5, 20),
    frozenset(("ZAG", "LJU")): (2.5, 12),
    frozenset(("MIL", "VCE")): (2.5, 20),
    frozenset(("MIL", "BLQ")): (1.2, 20),
    frozenset(("ROM", "NAP")): (1.2, 20),
    frozenset(("ROM", "FLR")): (1.6, 20),
    frozenset(("FLR", "PSA")): (1.0, 9),
    frozenset(("BRU", "AMS")): (2.5, 20),
    frozenset(("AMS", "EIN")): (1.3, 20),
    frozenset(("AMS", "RTM")): (0.7, 15),
    frozenset(("KRK", "KTW")): (1.3, 5),
    frozenset(("CPH", "MMA")): (0.6, 15),
    frozenset(("FRA", "CGN")): (1.2, 25),
    frozenset(("CGN", "DUS")): (0.6, 10),
    frozenset(("ZRH", "BSL")): (1.0, 20),
    frozenset(("SOF", "SKP")): (5.0, 20),
    frozenset(("VNO", "KUN")): (1.3, 8),
    frozenset(("RIX", "VNO")): (4.0, 15),
    frozenset(("RIX", "TLL")): (4.3, 15),
    frozenset(("BUS", "TBS")): (5.0, 12),
}


# Kara bağlantısı olmayan / pratikte geçilmeyen ülkeler (farklı ülkeye otobüsle gidilemez sayılır)
NO_LAND_LINK = {"CY", "GB", "IE", "IL", "AE", "IS", "MT", "AZ"}


@dataclass(frozen=True)
class Ground:
    from_city: str
    to_city: str
    km: float
    hours: float
    eur: float
    known_route: bool    # True: OVERRIDES'tan, False: formülle

    def cost(self, eur_rate: float) -> float:
        return self.eur * eur_rate


class GroundEstimator:
    def __init__(self, airports: AirportDB, base_eur: float = 5.0, eur_per_km: float = 0.06,
                 road_factor: float = 1.25, avg_kmh: float = 75.0):
        self.airports = airports
        self.base_eur = base_eur
        self.eur_per_km = eur_per_km
        self.road_factor = road_factor
        self.avg_kmh = avg_kmh

    def estimate(self, a: str, b: str) -> Ground | None:
        a, b = self.airports.city_of(a), self.airports.city_of(b)
        if a == b:
            return Ground(a, b, 0, 0, 0, True)
        km = self.airports.city_distance_km(a, b)
        if km is None:
            return None
        ov = OVERRIDES.get(frozenset((a, b)))
        if ov:
            return Ground(a, b, round(km), ov[0], ov[1], True)
        ca, cb = self.airports.country_of(a), self.airports.country_of(b)
        if ca != cb and (ca in NO_LAND_LINK or cb in NO_LAND_LINK):
            return None   # deniz/kapalı sınır: kara yoluyla gidilemez say
        if ca == "CY" or cb == "CY":
            return None   # Kıbrıs içi (Ercan-Larnaka) sınır geçişi; otomatik önerme
        road = km * self.road_factor
        hours = road / self.avg_kmh + 0.5
        eur = self.base_eur + road * self.eur_per_km
        return Ground(a, b, round(km), round(hours, 1), round(eur), False)
