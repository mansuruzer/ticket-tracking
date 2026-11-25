from __future__ import annotations

from ..config import Settings
from ..geo import AirportDB
from .base import Cache, Fare, FareSearch, Verifier
from .composite import CompositeProvider
from .ignav import IgnavProvider, IgnavVerifier
from .mock import MockProvider
from .travelpayouts import TravelpayoutsProvider
from .verifiers import DuffelVerifier, SerpApiGoogleFlights


def real_providers(settings: Settings, airports: AirportDB) -> list:
    """Anahtarı tanımlı gerçek kaynaklar. Travelpayouts "her yere" taramasını yapabilen tek kaynak."""
    out = []
    if settings.tp_token and settings.provider in ("auto", "travelpayouts"):
        out.append(TravelpayoutsProvider(settings.tp_token, settings.currency, settings.tp_market,
                                         settings.tp_rate_per_min, city_of=airports.city_of))
    # Ignav rota rota sorgulanır ve kotası sınırlıdır: Travelpayouts varken yalnız doğrulamada kullanılır
    if settings.ignav_key and (settings.provider == "ignav" or (settings.provider == "auto" and not out)):
        out.append(IgnavProvider(settings.ignav_key, settings.currency, settings.eur_rate, airports,
                                 settings.ignav_market))
    return out


def build_search(settings: Settings, airports: AirportDB, cache: Cache | None) -> FareSearch:
    if settings.provider == "mock":
        prov = MockProvider(airports, settings.currency, settings.eur_rate)
    else:
        prov = CompositeProvider(real_providers(settings, airports))
    return FareSearch(prov, cache, settings.cache_ttl_min)


def build_verifiers(settings: Settings, airports: AirportDB) -> list[Verifier]:
    out: list[Verifier] = []
    if settings.ignav_key:
        out.append(IgnavVerifier(settings.ignav_key, settings.currency, settings.eur_rate, airports,
                                 settings.ignav_market))
    if settings.serpapi_key:
        out.append(SerpApiGoogleFlights(settings.serpapi_key, settings.currency, airports))
    if settings.duffel_token:
        out.append(DuffelVerifier(settings.duffel_token, settings.currency, settings.eur_rate, airports))
    return out


__all__ = ["Fare", "FareSearch", "Verifier", "build_search", "build_verifiers"]
