from __future__ import annotations

from ..config import Settings
from ..geo import AirportDB
from .base import Cache, Fare, FareSearch, Verifier
from .mock import MockProvider
from .travelpayouts import TravelpayoutsProvider
from .verifiers import DuffelVerifier, SerpApiGoogleFlights


def build_search(settings: Settings, airports: AirportDB, cache: Cache | None) -> FareSearch:
    if settings.provider == "travelpayouts":
        prov = TravelpayoutsProvider(settings.tp_token, settings.currency, settings.tp_market,
                                     settings.tp_rate_per_min, city_of=airports.city_of)
    else:
        prov = MockProvider(airports, settings.currency, settings.eur_rate)
    return FareSearch(prov, cache, settings.cache_ttl_min)


def build_verifiers(settings: Settings, airports: AirportDB) -> list[Verifier]:
    out: list[Verifier] = []
    if settings.serpapi_key:
        out.append(SerpApiGoogleFlights(settings.serpapi_key, settings.currency, airports))
    if settings.duffel_token:
        out.append(DuffelVerifier(settings.duffel_token, settings.currency, settings.eur_rate, airports))
    return out


__all__ = ["Fare", "FareSearch", "Verifier", "build_search", "build_verifiers"]
