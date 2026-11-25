"""Birden çok fiyat kaynağını tek sağlayıcı gibi gösterir.

Her kaynağın ay sonuçları birleştirilir; aynı rota, gün ve firma için en ucuz fiyat kalır.
Bir kaynak hata verirse diğerleri çalışmaya devam eder.
"""
from __future__ import annotations

import logging

from .base import Fare

log = logging.getLogger(__name__)


class CompositeProvider:
    def __init__(self, providers: list):
        self.providers = providers
        self.name = "+".join(p.name for p in providers) or "none"

    def fetch_month(self, origin: str, destination: str | None, month: str) -> list[Fare]:
        best: dict[tuple, Fare] = {}
        for p in self.providers:
            try:
                fares = p.fetch_month(origin, destination, month)
            except Exception as e:  # noqa: BLE001 - bir kaynak düşerse ötekiler sürsün
                log.warning("%s kaynağı hata verdi: %s", p.name, e)
                continue
            for f in fares:
                k = (f.origin_city, f.destination_city, f.depart, f.airline or "")
                if k not in best or f.price < best[k].price:
                    best[k] = f
        return list(best.values())
