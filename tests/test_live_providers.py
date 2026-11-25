from datetime import date, timedelta

import pytest

from ucuzgez.config import load_settings
from ucuzgez.geo import AirportDB
from ucuzgez.planner import TripQuery
from ucuzgez.providers import build_search, build_verifiers
from ucuzgez.providers.base import Fare
from ucuzgez.providers.composite import CompositeProvider
from ucuzgez.providers.ignav import IgnavProvider, IgnavVerifier

AP = AirportDB.load()
D = date.today() + timedelta(days=20)


class Resp:
    def __init__(self, js, code=200):
        self._js, self.status_code, self.text = js, code, str(js)

    def json(self):
        return self._js


class FakeHttp:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        return self.handler(url, json)


def test_ignav_cheapest_dates_expands_city_and_converts():
    def h(url, body):
        assert url.endswith("/fares/cheapest-dates") and body["market"] == "TR"
        price = {"SAW": 1500, "IST": 1800}[body["origin"]]
        return Resp({"origin": body["origin"], "destination": body["destination"], "dates": [
            {"departure_date": D.isoformat(), "price": {"amount": price, "currency": "TRY"}, "status": "priced"},
            {"departure_date": (D + timedelta(days=1)).isoformat(), "price": None, "status": "no_price"},
            {"departure_date": (D + timedelta(days=2)).isoformat(), "price": {"amount": 30, "currency": "EUR"}, "status": "priced"},
            {"departure_date": (D + timedelta(days=3)).isoformat(), "price": {"amount": 30, "currency": "USD"}, "status": "priced"},
        ]})
    http = FakeHttp(h)
    p = IgnavProvider("k", "try", 50, AP, session=http)
    fares = p.fetch_month("IST", "VIE", D.strftime("%Y-%m"))
    assert {c[1]["origin"] for c in http.calls} == {"SAW", "IST"}
    assert all(c[2]["X-Api-Key"] == "k" for c in http.calls)
    prices = sorted(f.price for f in fares if f.depart == D)
    assert prices == [1500, 1800]
    assert any(f.price == 1500 for f in fares if f.depart == D + timedelta(days=2))   # 30 EUR * 50
    assert not any(f.depart == D + timedelta(days=3) for f in fares)                  # bilinmeyen kur atlandı
    assert p.fetch_month("IST", None, D.strftime("%Y-%m")) == []


def test_ignav_verifier_picks_cheapest_with_details():
    def h(url, body):
        assert url.endswith("/fares/one-way") and body["origin"] == "SAW"
        seg = lambda c, n, t, a: {"marketing_carrier_code": c, "flight_number": n, "departure_airport": "SAW",
                                  "departure_time_local": f"{D.isoformat()}T{t}", "arrival_airport": a}
        return Resp({"itineraries": [
            {"price": {"amount": 2100, "currency": "TRY"}, "outbound": {"duration_minutes": 140, "segments": [seg("TK", "1", "10:00:00", "VIE")]}},
            {"price": {"amount": 1650, "currency": "TRY"}, "outbound": {"duration_minutes": 135, "segments": [seg("PC", "331", "06:45:00", "VIE")]}},
        ]})
    v = IgnavVerifier("k", "try", 50, AP, session=FakeHttp(h))
    f = v.verify_oneway("SAW", "VIE", D)
    assert (f.price, f.airline, f.flight_number, f.depart_time, f.verified) == (1650, "PC", "PC331", "06:45", True)


def test_ignav_error_returns_nothing():
    v = IgnavVerifier("k", "try", 50, AP, session=FakeHttp(lambda u, b: Resp({"error": "x"}, 401)))
    assert v.verify_oneway("SAW", "VIE", D) is None


def _fare(src, price, airline="PC"):
    return Fare("SAW", "VIE", "IST", "VIE", D, price, "try", src, airline=airline)


def test_composite_merges_and_survives_errors():
    class P:
        def __init__(self, name, fares=None, boom=False):
            self.name, self.fares, self.boom = name, fares or [], boom

        def fetch_month(self, *a):
            if self.boom:
                raise RuntimeError("down")
            return self.fares
    c = CompositeProvider([P("a", [_fare("a", 2000), _fare("a", 1900, "TK")]), P("b", boom=True), P("c", [_fare("c", 1500)])])
    out = {(f.airline, f.price) for f in c.fetch_month("IST", "VIE", "2026-12")}
    assert out == {("PC", 1500), ("TK", 1900)} and c.name == "a+b+c"


@pytest.fixture
def settings(monkeypatch, tmp_path):
    for k in ("TRAVELPAYOUTS_TOKEN", "IGNAV_API_KEY", "SERPAPI_KEY", "DUFFEL_TOKEN", "UG_VERIFY_TOP_N"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("UG_PROVIDER", "auto")
    monkeypatch.setenv("UG_DB_PATH", str(tmp_path / "t.db"))
    return monkeypatch


def test_auto_selects_sources(settings):
    settings.setenv("TRAVELPAYOUTS_TOKEN", "t")
    settings.setenv("IGNAV_API_KEY", "i")
    s = load_settings()
    assert build_search(s, AP, None).provider.name == "tp"          # Ignav kotası doğrulamaya saklanır
    assert [v.name for v in build_verifiers(s, AP)] == ["ignav"] and s.verify_top_n == 3
    settings.delenv("TRAVELPAYOUTS_TOKEN")
    assert build_search(load_settings(), AP, None).provider.name == "ignav"


def test_no_source_gives_clear_error(settings):
    from ucuzgez.services import Services
    svc = Services(load_settings())
    q = TripQuery(["IST"], D, D + timedelta(days=5), 3, 5)
    with pytest.raises(ValueError, match="kaynağı bağlı değil"):
        svc.plan(q)
    settings.setenv("IGNAV_API_KEY", "i")
    svc = Services(load_settings())
    with pytest.raises(ValueError, match="Travelpayouts"):
        svc.plan(TripQuery(["IST"], D, D + timedelta(days=5), 3, 5))
