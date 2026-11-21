"""Telegram botu: kütüphanesiz, düz Bot API + long polling.

Kurulum: @BotFather'dan bot oluştur, token'ı TELEGRAM_BOT_TOKEN'a yaz.
Kullanıcı eşleme: her kullanıcıya `cli add-user` ile davet kodu verilir; Telegram'da
/start <kod> yazınca hesap bağlanır. Davetsiz kimse kullanamaz.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .commands import HELP, parse_search
from .services import Services

log = logging.getLogger(__name__)
MAX_LEN = 4000


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Telegram 4096 karakter sınırı: paragraf sınırlarından böl."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > limit and cur:
            out.append(cur)
            cur = ""
        while len(block) > limit:
            out.append(block[:limit])
            block = block[limit:]
        cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        out.append(cur)
    return out


class TelegramBot:
    def __init__(self, token: str, services: Services):
        self.api = f"https://api.telegram.org/bot{token}"
        self.svc = services
        self.http = requests.Session()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self._stop = threading.Event()

    # ---- gönderim -------------------------------------------------------------
    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        for part in split_message(text):
            body = {"chat_id": chat_id, "text": part, "parse_mode": "HTML",
                    "disable_web_page_preview": True}
            if reply_markup:
                body["reply_markup"] = reply_markup
            try:
                r = self.http.post(f"{self.api}/sendMessage", json=body, timeout=20)
                if r.status_code == 429:
                    time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)))
                    self.http.post(f"{self.api}/sendMessage", json=body, timeout=20)
            except requests.RequestException as e:
                log.warning("Telegram gönderim hatası: %s", e)

    def notify_user(self, user, text: str) -> None:
        if user["tg_chat_id"]:
            self.send(user["tg_chat_id"], text)

    # ---- döngü ----------------------------------------------------------------
    def run_forever(self) -> None:
        offset = self.svc.db.kv_get("tg_offset") or 0
        log.info("Telegram botu başladı")
        while not self._stop.is_set():
            try:
                r = self.http.get(f"{self.api}/getUpdates",
                                  params={"timeout": 30, "offset": offset,
                                          "allowed_updates": '["message"]'}, timeout=40)
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    self.svc.db.kv_set("tg_offset", offset)
                    msg = upd.get("message")
                    if msg:
                        self.pool.submit(self._safe_handle, msg)
            except Exception as e:
                log.warning("getUpdates hatası: %s", e)
                time.sleep(5)

    def stop(self) -> None:
        self._stop.set()

    def _safe_handle(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        try:
            reply = self.handle(msg)
            if reply:
                self.send(chat_id, reply)
        except ValueError as e:
            self.send(chat_id, f"⚠️ {e}")
        except Exception:
            log.exception("mesaj işlenemedi")
            self.send(chat_id, "⚠️ Beklenmeyen bir hata oldu, tekrar dene.")

    # ---- komutlar ---------------------------------------------------------------
    def handle(self, msg: dict) -> str | None:
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        db = self.svc.db
        user = db.user_by_chat(chat_id)

        if text.startswith("/start"):
            parts = text.split()
            if len(parts) == 2:
                u = db.link_telegram(parts[1], chat_id)
                if not u:
                    return "Davet kodu geçersiz."
                return (f"Merhaba {u['name']}! Hesabın bağlandı. 📍 /konum ile konumunu paylaş, "
                        f"sonra /kesfet yaz.\n\n" + HELP)
            return "Davet kodunla başla: /start KOD" if not user else HELP

        if not user:
            return "Bu bot davetle çalışıyor. Yöneticiden kod al: /start KOD"

        if "location" in msg:
            loc = msg["location"]
            db.set_home(user["id"], lat=loc["latitude"], lon=loc["longitude"])
            near = self.svc.airports.cities_near_point(loc["latitude"], loc["longitude"],
                                                       user["home_radius_km"] or 150)
            names = ", ".join(f"{self.svc.airports.city_name(c)} ({c}, {km:.0f} km)" for c, km in near[:3])
            return f"📍 Konum kaydedildi. Kalkış için kullanılacaklar: {names or 'yakında havalimanı yok, /yaricap artır'}"

        if not text.startswith("/"):
            return "Komut bekliyorum. /yardim"
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()

        if cmd in ("/yardim", "/help"):
            return HELP
        if cmd == "/konum":
            self.send(chat_id, "Aşağıdaki butona bas 👇", reply_markup={
                "keyboard": [[{"text": "📍 Konumumu paylaş", "request_location": True}]],
                "resize_keyboard": True, "one_time_keyboard": True})
            return None
        if cmd == "/ev":
            if not args:
                return "Kullanım: /ev IST"
            db.set_home(user["id"], code=args[0])
            return f"🏠 Ev havalimanı: {self.svc.airports.city_name(args[0])}"
        if cmd == "/yaricap":
            if not args or not args[0].isdigit():
                return "Kullanım: /yaricap 200 (km)"
            db.set_home(user["id"], radius_km=float(args[0]))
            return f"Yarıçap {args[0]} km."
        if cmd in ("/ara", "/kesfet"):
            if cmd == "/kesfet":
                args = ["konum", *args]
            return self._search(chat_id, user, args)
        if cmd == "/takip":
            return self._watch(user, args)
        if cmd == "/takipler":
            ws = db.watches(user["id"])
            if not ws:
                return "Takibin yok. Örnek: /takip IST 3-5 4000"
            cur = self.svc.s.currency
            from .formatting import money
            return "\n".join(
                f"#{w['id']} {w['name']} · hedef {money(w['threshold'], cur) if w['threshold'] else '-'} · "
                f"son en iyi {money(w['last_best'], cur) if w['last_best'] else '-'}"
                + ("" if w["active"] else " (kapalı)") for w in ws)
        if cmd == "/sil":
            if not args or not args[0].lstrip("#").isdigit():
                return "Kullanım: /sil 3"
            return "Silindi." if db.delete_watch(user["id"], int(args[0].lstrip("#"))) else "Bulunamadı."
        if cmd == "/radar":
            self.send(chat_id, "🎯 Taranıyor…")
            r = self.svc.radar(user)
            return r or ("Şu an olağan dışı ucuz bir şey yok (ya da yeterli fiyat geçmişi birikmedi; "
                         "radar birkaç gün veri topladıktan sonra anlamlı olur).")
        return "Bilinmeyen komut. /yardim"

    def _search(self, chat_id: int, user, args: list[str]) -> str:
        ps = parse_search(args)
        ps.query.origins = self.svc.resolve_origins(user, ps.origin_text)
        names = ", ".join(self.svc.airports.city_name(o) for o in ps.query.origins)
        self.send(chat_id, f"🔎 {names} çıkışlı, {ps.query.date_from:%d.%m}–{ps.query.date_to:%d.%m} "
                           f"arası, {ps.query.min_days}-{ps.query.max_days} günlük geziler aranıyor…")
        return self.svc.format_result(self.svc.plan(ps.query))

    def _watch(self, user, args: list[str]) -> str:
        if not args:
            return "Örnek: /takip IST 3-5 4000  veya  /takip IST 01.12 31.12 3-5 VIE PRG 5000"
        ps = parse_search(args)
        ps.query.origins = self.svc.resolve_origins(user, ps.origin_text)
        threshold = ps.budget
        ps.query.budget = None
        if len(self.svc.db.watches(user["id"])) >= 10:
            return "En fazla 10 takip açabilirsin. /takipler · /sil"
        dest = ",".join(ps.query.destinations) if ps.query.destinations else "her yer"
        when = "sürekli 60 gün" if ps.rolling_days else f"{ps.query.date_from:%d.%m}-{ps.query.date_to:%d.%m}"
        name = f"{'/'.join(ps.query.origins)}→{dest} {ps.query.min_days}-{ps.query.max_days}g ({when})"
        wid = self.svc.db.add_watch(user["id"], name,
                                    self.svc.query_to_params(ps.query, ps.rolling_days), threshold)
        return (f"✅ Takip #{wid} açıldı: {name}. Her ~{self.svc.s.watch_interval_min // 60} saatte kontrol "
                f"edeceğim; fiyat hedefin altına inerse ya da normalden belirgin ucuzlarsa yazacağım.")
