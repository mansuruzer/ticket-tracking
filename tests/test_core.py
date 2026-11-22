from datetime import date, timedelta

import pytest

from ucuzgez.commands import parse_search, parse_date
from ucuzgez.geo import get_airports
from ucuzgez.ground import GroundEstimator
from ucuzgez.planner import Planner, TripQuery
from ucuzgez.providers.base import Fare, FareSearch
from ucuzgez.providers.mock import MockProvider
from ucuzgez.providers.travelpayouts import TravelpayoutsProvider

AP = get_airports()
D0 = date.today() + timedelta(days=20)


# ---------------------------------------------------------------- komut ayrıştırma
def test_parse_basic():
    t = date(2026, 11, 21)
    ps = parse_search("IST 01.12 20.12 3-5 4.000 VIE PRG direkt pegasus".split(), today=t)
    q = ps.query
    assert ps.origin_text == "IST"
    assert (q.date_from, q.date_to) == (date(2026, 12, 1), date(2026, 12, 20))
    assert (q.min_days, q.max_days) == (3, 5)
    assert ps.budget == 4000 and q.destinations == ["VIE", "PRG"]
    assert q.max_transfers == 0 and q.airlines == {"PC"}


def test_parse_defaults_and_rolling():
    t = date(2026, 11, 21)
    ps = parse_search(["konum", "4", "2500"], today=t)
    assert ps.origin_text == "konum" and ps.rolling_days == 60
    assert (ps.query.min_days, ps.query.max_days) == (4, 4) and ps.budget == 2500
    assert ps.query.date_from == date(2026, 11, 22)


def test_parse_date_rolls_to_next_year():
    assert parse_date("05.01", today=date(2026, 11, 21)) == date(2027, 1, 5)
    assert parse_date("2026-12-05") == date(2026, 12, 5)
    with pytest.raises(ValueError):
        parse_search(["IST", "nonsense!"])


# ---------------------------------------------------------------- konum / kara yolu
def test_geo_and_ground():
    assert AP.city_of("SAW") == "IST"
    near = [c for c, _ in AP.cities_near_point(41.0, 29.0, 60)]
    assert near == ["IST"]
    g = GroundEstimator(AP)
    vb = g.estimate("VIE", "BTS")
    assert vb.known_route and vb.hours == 1.0
    assert g.estimate("ATH", "LCA") is None          # deniz
    assert g.estimate("LON", "PAR") is None          # GB kara bağlantısı yok sayılır
    est = g.estimate("BEG", "ZAG")
    assert est and not est.known_route and 3 < est.hours < 7


# ---------------------------------------------------------------- planlayıcı (elle senaryo)
class FakeProvider:
    name = "fake"

    def __init__(self, fares):
        self.fares = fares
        self.calls = []

    def fetch_month(self, origin, destination, month):
        self.calls.append((origin, destination, month))
        return [f for f in self.fares
                if f.origin_city == origin and (destination is None or f.destination_city == destination)
                and f.depart.strftime("%Y-%m") == month]


def F(o, d, day, price, transfers=0, airline="PC"):
    return Fare(o, d, o, d, day, price, "try", "fake", airline=airline, transfers=transfers)


def make_planner(fares, **kw):
    prov = FakeProvider(fares)
    return Planner(FareSearch(prov), AP, GroundEstimator(AP), eur_rate=50, **kw), prov


def test_open_jaw_beats_classic_return():
    d1, d2 = D0, D0 + timedelta(days=4)
    fares = [
        F("IST", "VIE", d1, 1000),
        F("VIE", "IST", d2, 4000),      # Viyana dönüşü pahalı
        F("PRG", "IST", d2, 1200),      # Prag dönüşü ucuz
    ]
    p, _ = make_planner(fares)
    res = p.plan(TripQuery(["IST"], d1, d1, 3, 5))
    best = res.trips[0]
    assert best.dest_city == "VIE" and best.return_city == "PRG" and best.days == 4
    assert best.ground_cost == 20 * 50                  # VIE-PRG tipik 20€ * kur
    assert best.total == 1000 + 1200 + 1000
    assert best.classic_total == 5000 and best.saving == 1800
    assert any(t.return_city == "VIE" for t in res.trips)   # klasik seçenek de listede


def test_open_jaw_disabled_and_budget_and_transfers():
    d1, d2 = D0, D0 + timedelta(days=3)
    fares = [F("IST", "VIE", d1, 1000), F("VIE", "IST", d2, 4000), F("PRG", "IST", d2, 1200),
             F("IST", "BUD", d1, 500, transfers=1), F("BUD", "IST", d2, 500)]
    p, _ = make_planner(fares)
    res = p.plan(TripQuery(["IST"], d1, d1, 3, 3, open_jaw=False, max_transfers=0))
    assert [t.return_city for t in res.trips] == ["VIE"]
    res = p.plan(TripQuery(["IST"], d1, d1, 3, 3, budget=1500))
    assert [t.dest_city for t in res.trips] == ["BUD"]


def test_days_window_respected():
    d1 = D0
    fares = [F("IST", "VIE", d1, 1000), F("VIE", "IST", d1 + timedelta(days=2), 100),
             F("VIE", "IST", d1 + timedelta(days=9), 100)]
    p, _ = make_planner(fares)
    assert p.plan(TripQuery(["IST"], d1, d1, 3, 7)).trips == []


class FakeVerifier:
    name = "v"

    def verify_oneway(self, o, d, day):
        return Fare(o, d, AP.city_of(o), AP.city_of(d), day, 777, "try", "live", verified=True)


def test_verification_replaces_prices():
    d1, d2 = D0, D0 + timedelta(days=3)
    p, _ = make_planner([F("IST", "VIE", d1, 1000), F("VIE", "IST", d2, 1000)],
                        verifiers=[FakeVerifier()], verify_top_n=1)
    t = p.plan(TripQuery(["IST"], d1, d1, 3, 3, open_jaw=False)).trips[0]
    assert t.verified and t.total == 777 * 2


def test_cache_avoids_repeat_calls():
    class MemCache(dict):
        def cache_get(self, k, age): return self.get(k)
        def cache_set(self, k, v): self[k] = v
    prov = FakeProvider([F("IST", "VIE", D0, 1)])
    s = FareSearch(prov, MemCache())
    s.search_oneway("IST", None, D0, D0)
    s.search_oneway("IST", None, D0, D0)
    assert len(prov.calls) == 1 and s.cache_hits == 1


def test_mock_full_run_has_diversity():
    p = Planner(FareSearch(MockProvider(AP)), AP, GroundEstimator(AP), 54)
    res = p.plan(TripQuery(["IST"], D0, D0 + timedelta(days=20), 3, 6))
    assert 0 < len(res.trips) <= 10
    from collections import Counter
    assert max(Counter(t.dest_city for t in res.trips).values()) <= 2
    assert res.trips == sorted(res.trips, key=lambda t: t.total)


# ---------------------------------------------------------------- Travelpayouts ayrıştırma
class FakeResp:
    status_code = 200
    def __init__(self, js): self._js = js
    def json(self): return self._js
    def raise_for_status(self): pass


class FakeSession:
    def __init__(self): self.params = None
    def get(self, url, params=None, headers=None, timeout=None):
        self.params = params
        return FakeResp({"success": True, "data": [{
            "origin": "IST", "destination": "VIE", "origin_airport": "SAW", "destination_airport": "VIE",
            "price": 1899, "airline": "PC", "flight_number": "381", "departure_at": "2026-12-05T07:15:00+03:00",
            "transfers": 0, "duration_to": 150, "link": "/search/IST0512VIE1?t=x"}]})


def test_travelpayouts_parse():
    sess = FakeSession()
    tp = TravelpayoutsProvider("tok", "try", "tr", 1000, session=sess)
    fares = tp.fetch_month("IST", None, "2026-12")
    assert "destination" not in sess.params and sess.params["one_way"] == "true"
    f = fares[0]
    assert (f.origin, f.origin_city, f.destination_city) == ("SAW", "IST", "VIE")
    assert f.price == 1899 and f.airline == "PC" and f.depart == date(2026, 12, 5) and f.depart_time == "07:15"
    assert f.link.startswith("https://www.aviasales.com/search/")


# ---------------------------------------------------------------- bölgeler
def test_region_parse_and_filter():
    from ucuzgez.regions import find_region
    assert find_region("Schengen").key == "schengen" and find_region("vizesiz").key == "vizesiz"
    assert find_region("HER") is None          # Kandiye havalimanı kodu, bölge değil
    ps = parse_search("IST 3-5 vizesiz".split())
    assert ps.query.include_countries and "RS" in ps.query.include_countries and "ME" not in ps.query.include_countries
    assert "AT" not in ps.query.include_countries
    # Belgrad'a git; Budapeşte'den dönüş ucuz ama Schengen (vize!) -> vizesiz aramada önerilmemeli
    d1, d2 = D0, D0 + timedelta(days=4)
    fares = [F("IST", "BEG", d1, 800), F("BEG", "IST", d2, 3000), F("BUD", "IST", d2, 500), F("IST", "VIE", d1, 300),
             F("VIE", "IST", d2, 300)]
    p, _ = make_planner(fares)
    q = TripQuery(["IST"], d1, d1, 3, 5, include_countries=set(ps.query.include_countries))
    trips = p.plan(q).trips
    assert trips and all(t.dest_city == "BEG" and t.return_city == "BEG" for t in trips)
    q2 = TripQuery(["IST"], d1, d1, 3, 5)
    assert p.plan(q2).trips[0].dest_city == "VIE"
