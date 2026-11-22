"""Gezi planlayıcı: nereye gidilir, kaç gün kalınır, hangi şehirden dönülür?

Akış
1. Kalkış şehir(ler)inden tarih penceresinde HER YERE (veya verilen yerlere)
   tek yön en ucuz uçuşlar çekilir.
2. En ucuz K varış şehri aday olur.
3. Her aday D için dönüş şehirleri: D'nin kendisi + kara yoluyla `max_ground_hours`
   içinde ulaşılabilen komşu şehirler (Viyana -> Prag, Bratislava, Budapeşte...).
4. Her dönüş şehri R'den eve tek yön uçuşlar çekilir.
5. gidiş(gün1) + dönüş(gün1 + n) + kara yolu tahmini, n ∈ [min_gün, max_gün]
   kombinasyonları puanlanır; aynı tarihlerdeki "klasik" (D'den dönüş) fiyatla
   kıyaslanıp tasarruf gösterilir.
6. (İsteğe bağlı) en iyi N sonuç canlı fiyatla doğrulanır.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .geo import AirportDB
from .ground import Ground, GroundEstimator
from .providers.base import Fare, FareSearch, Verifier


@dataclass
class TripQuery:
    origins: list[str]
    date_from: date                     # en erken gidiş
    date_to: date                       # en geç gidiş
    min_days: int = 3
    max_days: int = 7
    destinations: list[str] | None = None   # None = her yer
    budget: float | None = None
    open_jaw: bool = True
    max_ground_hours: float = 6.0
    max_transfers: int = 1
    airlines: set[str] | None = None
    exclude_airlines: set[str] | None = None
    exclude_countries: set[str] | None = None
    include_countries: set[str] | None = None    # bölge/ülke filtresi (örn. vizesiz ülkeler)
    include_cities: set[str] | None = None       # ayrıca dahil edilen şehirler (ülke filtresiyle VEYA)
    exclude_cities: set[str] | None = None
    region: str | None = None                    # gösterim için bölge anahtarı
    top_destinations: int = 12
    limit: int = 10
    per_destination: int = 2

    def validate(self) -> None:
        if not self.origins:
            raise ValueError("En az bir kalkış noktası gerekli")
        if self.date_to < self.date_from:
            raise ValueError("Bitiş tarihi başlangıçtan önce olamaz")
        if (self.date_to - self.date_from).days > 120:
            raise ValueError("Gidiş penceresi en fazla 120 gün olabilir")
        if not (1 <= self.min_days <= self.max_days <= 30):
            raise ValueError("Gün aralığı 1-30 arasında olmalı (min <= max)")


@dataclass
class Trip:
    outbound: Fare
    inbound: Fare
    ground: Ground | None
    ground_cost: float
    total: float
    days: int
    classic_total: float | None = None     # aynı tarihlerde D'den dönseydin

    @property
    def dest_city(self) -> str:
        return self.outbound.destination_city

    @property
    def return_city(self) -> str:
        return self.inbound.origin_city

    @property
    def open_jaw(self) -> bool:
        return self.dest_city != self.return_city

    @property
    def saving(self) -> float | None:
        if self.classic_total is None or not self.open_jaw:
            return None
        s = self.classic_total - self.total
        return s if s > 0 else None

    @property
    def key(self) -> str:
        o, i = self.outbound, self.inbound
        return f"{o.origin_city}-{o.destination_city}@{o.depart}|{i.origin_city}-{i.destination_city}@{i.depart}"

    @property
    def verified(self) -> bool:
        return self.outbound.verified and self.inbound.verified


@dataclass
class PlanResult:
    trips: list[Trip]
    api_calls: int
    cache_hits: int
    destinations_scanned: int
    notes: list[str] = field(default_factory=list)


class Planner:
    def __init__(self, search: FareSearch, airports: AirportDB, ground: GroundEstimator,
                 eur_rate: float, verifiers: list[Verifier] | None = None, verify_top_n: int = 0):
        self.search = search
        self.airports = airports
        self.ground = ground
        self.eur_rate = eur_rate
        self.verifiers = verifiers or []
        self.verify_top_n = verify_top_n

    # ------------------------------------------------------------------
    def _fares(self, o: str, d: str | None, d1: date, d2: date, q: TripQuery) -> list[Fare]:
        return self.search.search_oneway(o, d, d1, d2, q.max_transfers, q.airlines, q.exclude_airlines)

    def plan(self, q: TripQuery) -> PlanResult:
        q.validate()
        calls0, hits0 = self.search.api_calls, self.search.cache_hits
        city = self.airports.city_of
        origins = list(dict.fromkeys(city(o) for o in q.origins))
        home = set(origins)
        notes: list[str] = []

        # 1) gidiş uçuşları: şehir -> gün -> en ucuz
        best_out: dict[str, dict[date, Fare]] = defaultdict(dict)
        targets = [city(d) for d in q.destinations] if q.destinations else [None]
        for o in origins:
            for t in targets:
                for f in self._fares(o, t, q.date_from, q.date_to, q):
                    dc = city(f.destination_city)
                    if dc in home:
                        continue
                    if not self._country_ok(dc, q):
                        continue
                    cur = best_out[dc].get(f.depart)
                    if cur is None or f.price < cur.price:
                        best_out[dc][f.depart] = f
        if not best_out:
            return PlanResult([], self.search.api_calls - calls0, self.search.cache_hits - hits0, 0,
                              ["Bu tarihlerde gidiş uçuşu bulunamadı."])

        # 2) en ucuz K şehir
        ranked = sorted(best_out, key=lambda c: min(f.price for f in best_out[c].values()))
        candidates = ranked[: q.top_destinations]

        # 3-4) dönüşler
        in_from = q.date_from + timedelta(days=q.min_days)
        in_to = q.date_to + timedelta(days=q.max_days)
        inbound_memo: dict[str, dict[date, Fare]] = {}

        def inbound(r: str) -> dict[date, Fare]:
            if r not in inbound_memo:
                m: dict[date, Fare] = {}
                for h in origins:
                    for f in self._fares(r, h, in_from, in_to, q):
                        if city(f.destination_city) not in home:
                            continue
                        if f.depart not in m or f.price < m[f.depart].price:
                            m[f.depart] = f
                inbound_memo[r] = m
            return inbound_memo[r]

        max_km = q.max_ground_hours * 75 / 1.25 + 50
        all_trips: list[Trip] = []
        for D in candidates:
            options: list[tuple[str, Ground | None]] = [(D, None)]
            if q.open_jaw:
                near = []
                for R, _km in self.airports.cities_near_city(D, max_km):
                    if R in home or not self._return_ok(R, q):
                        continue   # dönüş şehri de seçilen ülkelerde olmalı (vize!) ve hariç tutulmamalı
                    g = self.ground.estimate(D, R)
                    if g and g.hours <= q.max_ground_hours:
                        near.append((R, g))
                # önce bilinen büyük şehirler, sonra yakınlık (küçük bölgesel havalimanları sona)
                near.sort(key=lambda x: (x[0] not in self.airports.popular, x[1].hours))
                options += near[:10]
            ins_D = inbound(D)
            for R, g in options:
                ins = ins_D if R == D else inbound(R)
                if not ins:
                    continue
                gcost = round(g.cost(self.eur_rate)) if g else 0.0
                for d1, fo in best_out[D].items():
                    for n in range(q.min_days, q.max_days + 1):
                        d2 = d1 + timedelta(days=n)
                        fi = ins.get(d2)
                        if not fi:
                            continue
                        total = fo.price + fi.price + gcost
                        classic = None
                        if R != D and d2 in ins_D:
                            classic = fo.price + ins_D[d2].price
                        all_trips.append(Trip(fo, fi, g, gcost, total, n, classic))

        # 5) sırala + çeşitlendir
        all_trips.sort(key=lambda t: t.total)
        chosen = self._diversify(all_trips, q)

        # 6) canlı doğrulama
        if self.verifiers and self.verify_top_n and chosen:
            chosen = self._verify(chosen[: self.verify_top_n]) + chosen[self.verify_top_n:]
            chosen.sort(key=lambda t: t.total)
            notes.append(f"İlk {min(self.verify_top_n, len(chosen))} sonuç canlı fiyatla kontrol edildi.")

        if q.budget and not chosen and all_trips:
            notes.append(f"Bütçe altında sonuç yok; bulunan en ucuz: {all_trips[0].total:.0f}")
        return PlanResult(chosen, self.search.api_calls - calls0, self.search.cache_hits - hits0,
                          len(candidates), notes)

    def _country_ok(self, city: str, q: TripQuery) -> bool:
        """Şehir seçilen hedeflere uyuyor mu? (dahil: ülke VEYA şehir; sonra hariçler)"""
        city = self.airports.city_of(city)
        c = self.airports.country_of(city)
        if q.exclude_cities and city in q.exclude_cities:
            return False
        if q.exclude_countries and c in q.exclude_countries:
            return False
        if q.include_countries is None and q.include_cities is None:
            return True
        return bool((q.include_countries and c in q.include_countries)
                    or (q.include_cities and city in q.include_cities))

    def _return_ok(self, city: str, q: TripQuery) -> bool:
        """Dönüş şehri: yalnız şehir seçildiyse (ülke/bölge yok) hariçler dışında her komşu olur."""
        if q.include_countries is None and q.include_cities is not None:
            c = self.airports.country_of(city)
            return not ((q.exclude_cities and self.airports.city_of(city) in q.exclude_cities)
                        or (q.exclude_countries and c in q.exclude_countries))
        return self._country_ok(city, q)

    # ------------------------------------------------------------------
    @staticmethod
    def _diversify(trips: list[Trip], q: TripQuery) -> list[Trip]:
        per_dest: dict[str, int] = defaultdict(int)
        seen_pair_dates: set[tuple] = set()
        out: list[Trip] = []
        for t in trips:
            if q.budget and t.total > q.budget:
                break
            if per_dest[t.dest_city] >= q.per_destination:
                continue
            sig = (t.dest_city, t.return_city, t.outbound.depart)
            if sig in seen_pair_dates:
                continue
            seen_pair_dates.add(sig)
            per_dest[t.dest_city] += 1
            out.append(t)
            if len(out) >= q.limit:
                break
        return out

    def _verify(self, trips: list[Trip]) -> list[Trip]:
        memo: dict[tuple, Fare | None] = {}

        def live(f: Fare) -> Fare:
            k = (f.origin, f.destination, f.depart)
            if k not in memo:
                memo[k] = None
                for v in self.verifiers:
                    r = v.verify_oneway(f.origin, f.destination, f.depart)
                    if r:
                        r.link = r.link or f.link
                        memo[k] = r
                        break
            return memo[k] or f

        out = []
        for t in trips:
            o, i = live(t.outbound), live(t.inbound)
            out.append(Trip(o, i, t.ground, t.ground_cost, o.price + i.price + t.ground_cost, t.days,
                            t.classic_total))
        return out
