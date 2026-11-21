"""Sunucusuz mod (Vercel): arka plan iş parçacığı YOK.

- Telegram: webhook -> POST /tg/webhook   (long polling yerine)
- Zamanlayıcı: GET /cron   (Vercel Cron günde 1 + GitHub Actions 3 saatte 1 tetikler)
- Veritabanı: Turso (TURSO_DATABASE_URL + TURSO_AUTH_TOKEN). Yoksa /tmp'de geçici SQLite (kalıcı DEĞİL).
- Kurulumdan sonra bir kez: GET /tg/kur?key=CRON_SECRET  -> webhook'u kaydeder.
"""
from __future__ import annotations

import logging
import os
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import load_settings
from .scheduler import Scheduler
from .services import Services
from .telegram_bot import TelegramBot
from .web import create_web_app

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ucuzgez.serverless")

settings = load_settings()
if not settings.turso_url and not os.environ.get("UG_DB_PATH"):
    settings.db_path = "/tmp/ucuzgez.db"   # Vercel'de yazılabilen tek yer (geçici!)
    log.warning("TURSO_DATABASE_URL yok: veriler kalıcı değil!")

if "localhost" in settings.base_url and os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
    settings.base_url = "https://" + os.environ["VERCEL_PROJECT_PRODUCTION_URL"]

svc = Services(settings)
bot = TelegramBot(settings.telegram_token, svc) if settings.telegram_token else None
scheduler = Scheduler(svc, lambda u, t: bot.notify_user(u, t) if (bot and u) else None, per_tick=50)
CRON_BUDGET_S = float(os.environ.get("UG_CRON_BUDGET_S", "240"))   # Vercel Hobby sınırı 300 sn


def _authorized(request: Request) -> bool:
    if not settings.cron_secret:
        return False
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip() \
        or request.query_params.get("key", "")
    return secrets.compare_digest(given, settings.cron_secret)


async def tg_webhook(request: Request):
    if not bot:
        return JSONResponse({"ok": False, "error": "TELEGRAM_BOT_TOKEN yok"}, status_code=503)
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not settings.tg_webhook_secret or not secrets.compare_digest(got, settings.tg_webhook_secret):
        return JSONResponse({"ok": False}, status_code=403)
    upd = await request.json()
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(bot.process_update, upd)
    return JSONResponse({"ok": True})


async def tg_setup(request: Request):
    if not _authorized(request):
        return JSONResponse({"error": "yetkisiz"}, status_code=401)
    if not bot or not settings.tg_webhook_secret:
        return JSONResponse({"error": "TELEGRAM_BOT_TOKEN ve TELEGRAM_WEBHOOK_SECRET gerekli"}, status_code=400)
    base = settings.base_url.rstrip("/")
    return JSONResponse(bot.set_webhook(f"{base}/tg/webhook", settings.tg_webhook_secret))


async def cron(request: Request):
    if not _authorized(request):
        return JSONResponse({"error": "yetkisiz"}, status_code=401)
    from starlette.concurrency import run_in_threadpool
    stats = await run_in_threadpool(scheduler.tick, None, CRON_BUDGET_S)
    return JSONResponse({"ok": True, **stats})


async def admin_add_user(request: Request):
    """İlk kurulumda terminal olmadan kullanıcı eklemek için: /yonetim/kullanici?key=CRON_SECRET&ad=Ali"""
    if not _authorized(request):
        return JSONResponse({"error": "yetkisiz"}, status_code=401)
    name = request.query_params.get("ad", "").strip()
    if not name:
        return JSONResponse({"kullanicilar": [
            {"id": u["id"], "ad": u["name"], "telegram": bool(u["tg_chat_id"]), "davet": u["tg_invite"]}
            for u in svc.db.all_users()]})
    if svc.db.count_users() >= 50:
        return JSONResponse({"error": "50 kullanıcı sınırı"}, status_code=400)
    u = svc.db.add_user(name)
    base = settings.base_url.rstrip("/")
    return JSONResponse({"ad": u["name"], "web_giris": f"{base}/giris?t={u['token']}",
                         "telegram": f"/start {u['tg_invite']}"})


app = create_web_app(svc, extra_routes=[
    Route("/tg/webhook", tg_webhook, methods=["POST"]),
    Route("/tg/kur", tg_setup),
    Route("/cron", cron, methods=["GET", "POST"]),
    Route("/yonetim/kullanici", admin_add_user),
])
