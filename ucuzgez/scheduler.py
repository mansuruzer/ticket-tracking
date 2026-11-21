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
from datetime import datetime

from .services import Services

log = logging.getLogger(__name__)


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

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        db = self.svc.db
        for w in db.due_watches(self.svc.s.watch_interval_min * 60)[: self.per_tick]:
            msg = self.svc.check_watch(w)
            if msg:
                self.notify(db.user(w["user_id"]), msg)

        today = now.date().isoformat()
        if self.svc.s.radar_enabled and now.hour >= self.svc.s.radar_hour and db.kv_get("radar_day") != today:
            db.kv_set("radar_day", today)
            for u in db.all_users():
                if not u["tg_chat_id"]:
                    continue
                try:
                    msg = self.svc.radar(u)
                    if msg:
                        self.notify(u, msg)
                except Exception:
                    log.exception("radar hata (kullanıcı %s)", u["id"])
            db.cache_purge()
