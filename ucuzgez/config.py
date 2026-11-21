"""Ayarlar: hepsi ortam değişkenlerinden (.env) okunur."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Basit .env okuyucu (python-dotenv bağımlılığı olmadan)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _get(k: str, d: str = "") -> str:
    return os.environ.get(k, d)


@dataclass
class Settings:
    db_path: str
    provider: str            # "travelpayouts" | "mock"
    tp_token: str
    tp_market: str
    tp_rate_per_min: int
    currency: str            # "try", "eur", "usd"...
    eur_rate: float          # 1 EUR kaç birim `currency` (kara yolu tahminini çevirmek için)
    serpapi_key: str
    duffel_token: str
    verify_top_n: int
    telegram_token: str
    base_url: str
    watch_interval_min: int
    radar_enabled: bool
    radar_hour: int
    cache_ttl_min: int
    ground_base_eur: float
    ground_eur_per_km: float
    max_ground_hours: float
    turso_url: str
    turso_token: str
    cron_secret: str
    tg_webhook_secret: str


def load_settings() -> Settings:
    _load_dotenv()
    cur = _get("UG_CURRENCY", "try").lower()
    return Settings(
        db_path=_get("UG_DB_PATH", "ucuzgez.db"),
        provider=_get("UG_PROVIDER", "mock").lower(),
        tp_token=_get("TRAVELPAYOUTS_TOKEN"),
        tp_market=_get("TRAVELPAYOUTS_MARKET", "tr"),
        tp_rate_per_min=int(_get("TRAVELPAYOUTS_RATE_PER_MIN", "50")),
        currency=cur,
        # Varsayılan ~Kasım 2026 kuru; .env'de güncelle. Sadece kara yolu tahmini için kullanılır.
        eur_rate=float(_get("UG_EUR_RATE", {"eur": "1", "try": "54", "usd": "1.17"}.get(cur, "1"))),
        serpapi_key=_get("SERPAPI_KEY"),
        duffel_token=_get("DUFFEL_TOKEN"),
        verify_top_n=int(_get("UG_VERIFY_TOP_N", "0")),
        telegram_token=_get("TELEGRAM_BOT_TOKEN"),
        base_url=_get("UG_BASE_URL", "http://localhost:8000"),
        watch_interval_min=int(_get("UG_WATCH_INTERVAL_MIN", "180")),
        radar_enabled=_get("UG_RADAR", "1") == "1",
        radar_hour=int(_get("UG_RADAR_HOUR", "9")),
        cache_ttl_min=int(_get("UG_CACHE_TTL_MIN", "360")),
        ground_base_eur=float(_get("UG_GROUND_BASE_EUR", "5")),
        ground_eur_per_km=float(_get("UG_GROUND_EUR_PER_KM", "0.06")),
        max_ground_hours=float(_get("UG_MAX_GROUND_HOURS", "6")),
        turso_url=_get("TURSO_DATABASE_URL"),
        turso_token=_get("TURSO_AUTH_TOKEN"),
        cron_secret=_get("CRON_SECRET"),
        tg_webhook_secret=_get("TELEGRAM_WEBHOOK_SECRET"),
    )
