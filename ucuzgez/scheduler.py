"""Arka plan zamanlayıcı — cronjob yerine uygulamanın içinde çalışır.

Her dakika uyanır:
- Süresi gelen takipleri (en fazla 5 tane / tur) kontrol eder -> Telegram'a yazar.
- Günde bir kez (UG_RADAR_HOUR saatinde) her kullanıcı için fırsat radarını çalıştırır.
- Günde bir kez eski önbelleği temizler.
Tek süreçte çalıştığı için ek bir cron/Redis/Celery gerekmez. 50 kullanıcı için yeterli.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .services import Services

log = logging.getLogger(__name__)


def local_now() -> datetime:
    """Sunucu UTC'de olsa bile Türkiye saatini kullan (UG_TZ ile değiştirilebilir)."""
    import os
    from datetime import timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.environ.get("UG_TZ", "Europe/Istanbul"))).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)


class Scheduler:
    def __init__(self, svc: Services, notify, tick_s: int = 60, per_tick: int = 5):
        """notify(user_row, text) -> None  (genelde TelegramBot.notify_user)"""
        self.svc = svc
        self.notify = notify
        self.tick_s = tick_s
        self.per_tick = per_tick
        self._stop = threading.Event()

    def run_forever(self) -> None:
        log.info("Zamanlayıcı başladı")
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception:
                log.exception("zamanlayıcı turu hata verdi")

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: datetime | None = None, deadline_s: float | None = None) -> dict:
        """Bir tur. deadline_s: sunucusuz modda süre sınırı (saniye) — aşılmadan durur."""
        start = time.monotonic()
        out_of_time = lambda: deadline_s is not None and time.monotonic() - start > deadline_s
        now = now or local_now()
        db = self.svc.db
        stats = {"watches": 0, "radar_users": 0}
        for w in db.due_watches(self.svc.s.watch_interval_min * 60)[: self.per_tick]:
            if out_of_time():
                return stats
            stats["watches"] += 1
            msg = self.svc.check_watch(w)
            if msg:
                self.notify(db.user(w["user_id"]), msg)

        today = now.date().isoformat()
        if self.svc.s.radar_enabled and now.hour >= self.svc.s.radar_hour and db.kv_get("radar_day") != today:
            db.kv_set("radar_day", today)
            for u in db.all_users():
                if not u["tg_chat_id"]:
                    continue
                if out_of_time():
                    db.kv_set("radar_day", None)   # bitmedi, bir sonraki turda devam
                    return stats
                if db.last_notified(u["id"], "radar_run", today, 86400):
                    continue                       # bu kullanıcı bugün tarandı
                db.mark_notified(u["id"], "radar_run", today, 0)
                stats["radar_users"] += 1
                try:
                    msg = self.svc.radar(u)
                    if msg:
                        self.notify(u, msg)
                except Exception:
                    log.exception("radar hata (kullanıcı %s)", u["id"])
            db.cache_purge()
        return stats
