"""Tek süreç: web + Telegram botu + zamanlayıcı.

Çalıştır:  uvicorn ucuzgez.app:app --host 0.0.0.0 --port 8000
           (ya da: python -m ucuzgez)
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from .config import load_settings
from .scheduler import Scheduler
from .services import Services
from .telegram_bot import TelegramBot
from .web import create_web_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ucuzgez")

settings = load_settings()
services = Services(settings)
bot = TelegramBot(settings.telegram_token, services) if settings.telegram_token else None


def _notify(user, text: str) -> None:
    if bot and user:
        bot.notify_user(user, text)
    else:
        log.info("Bildirim (Telegram kapalı) -> %s: %s", user["name"] if user else "?", text[:200])


scheduler = Scheduler(services, _notify)


@asynccontextmanager
async def lifespan(_app):
    if settings.provider == "mock":
        log.warning("UG_PROVIDER=mock: deneme verisi kullanılıyor. Gerçek fiyatlar için UG_PROVIDER=auto ve anahtarları ayarla.")
    threading.Thread(target=scheduler.run_forever, daemon=True, name="scheduler").start()
    if bot:
        threading.Thread(target=bot.run_forever, daemon=True, name="telegram").start()
    else:
        log.warning("TELEGRAM_BOT_TOKEN yok: bildirimler sadece loga yazılır.")
    yield
    scheduler.stop()
    if bot:
        bot.stop()


app = create_web_app(services, lifespan=lifespan)
