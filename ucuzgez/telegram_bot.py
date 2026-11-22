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
from .regions import find_region
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
    UPDATES = ["message", "callback_query"]

    def run_forever(self) -> None:
        import json as _json
        offset = self.svc.db.kv_get("tg_offset") or 0
        log.info("Telegram botu başladı")
        while not self._stop.is_set():
            try:
                r = self.http.get(f"{self.api}/getUpdates",
                                  params={"timeout": 30, "offset": offset,
                                          "allowed_updates": _json.dumps(self.UPDATES)}, timeout=40)
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    self.svc.db.kv_set("tg_offset", offset)
                    self.pool.submit(self._dispatch, upd)
            except Exception as e:
                log.warning("getUpdates hatası: %s", e)
                time.sleep(5)

    def stop(self) -> None:
        self._stop.set()

    # ---- webhook modu (Vercel / sunucusuz) -----------------------------------------
    def process_update(self, upd: dict) -> None:
        """Telegram aynı güncellemeyi tekrar gönderirse (zaman aşımı) ikinci kez işleme."""
        uid = int(upd.get("update_id", 0))
        if uid <= int(self.svc.db.kv_get("tg_last_update") or 0):
            return
        self.svc.db.kv_set("tg_last_update", uid)
        self._dispatch(upd)

    def set_webhook(self, url: str, secret: str) -> dict:
        r = self.http.post(f"{self.api}/setWebhook", json={
            "url": url, "secret_token": secret, "allowed_updates": self.UPDATES,
            "max_connections": 5}, timeout=20)
        return r.json()

    def _dispatch(self, upd: dict) -> None:
        if upd.get("message"):
            self._safe_handle(upd["message"])
        elif upd.get("callback_query"):
            cq = upd["callback_query"]
            chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
            try:
                self.http.post(f"{self.api}/answerCallbackQuery", json={"callback_query_id": cq["id"]}, timeout=10)
            except requests.RequestException:
                pass
            if chat_id is None:
                return
            try:
                reply = self.handle_callback(chat_id, cq.get("data") or "")
                if reply:
                    self.send(chat_id, *reply) if isinstance(reply, tuple) else self.send(chat_id, reply)
            except ValueError as e:
                self.send(chat_id, f"Olmadı: {e}")
            except Exception:
                log.exception("buton işlenemedi")
                self.send(chat_id, "Bir şeyler ters gitti. Birazdan tekrar dene.")

    def _safe_handle(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        try:
            reply = self.handle(msg)
            if reply:
                self.send(chat_id, *reply) if isinstance(reply, tuple) else self.send(chat_id, reply)
        except ValueError as e:
            self.send(chat_id, f"Olmadı: {e}\nÖrnekler için /yardim yaz.")
        except Exception:
            log.exception("mesaj işlenemedi")
            self.send(chat_id, "Bir şeyler ters gitti. Birazdan tekrar dene.")

    # ---- butonlar ---------------------------------------------------------------
    @staticmethod
    def region_keyboard(days: str) -> dict:
        from .regions import REGIONS
        keys = ["yurtdisi", "vizesiz", "kolay-vize", "schengen", "balkanlar", "kafkasya", "ortadogu", "yurtici"]
        btns = [{"text": REGIONS[k].label, "callback_data": f"k:{k}:{days}"} for k in keys]
        return {"inline_keyboard": [btns[i:i + 2] for i in range(0, len(btns), 2)]}

    @staticmethod
    def result_keyboard() -> dict:
        return {"inline_keyboard": [[{"text": "Bu aramayı takip et", "callback_data": "t:last"},
                                     {"text": "Başka bölge", "callback_data": "k:menu"}]]}

    def handle_callback(self, chat_id: int, data: str):
        user = self.svc.db.user_by_chat(chat_id)
        if not user:
            return "Bu bot davetle çalışıyor. Yöneticiden kod al: /start KOD"
        parts = data.split(":")
        if parts[0] == "k":
            if parts[1] == "menu":
                last = self.svc.db.kv_get(f"lastdays:{chat_id}") or "3-7"
                return ("Nereye bakalım?", self.region_keyboard(last))
            return self._search(chat_id, user, ["konum", parts[1], parts[2] if len(parts) > 2 else "3-7"])
        if parts[0] == "t":
            params = self.svc.db.kv_get(f"lastq:{chat_id}")
            if not params:
                return "Takip edilecek bir arama bulunamadı. Önce bir arama yap."
            if len(self.svc.db.watches(user["id"])) >= 10:
                return "En fazla 10 takip açabilirsin. /takipler ile bak, /sil ile yer aç."
            q = self.svc.params_to_query(params)
            name = self._watch_name(q, rolling=True)
            wid = self.svc.db.add_watch(user["id"], name, params, None)
            return (f"Takip #{wid} açıldı: {name}.\nFiyat son 30 günün normalinden belirgin düşerse yazacağım. "
                    f"Hedef fiyat koymak istersen: /takip {' '.join(q.origins)} {q.min_days}-{q.max_days} "
                    f"{q.region or ''} HEDEF")
        return None

    # ---- komutlar ---------------------------------------------------------------
    def handle(self, msg: dict):
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        db = self.svc.db
        user = db.user_by_chat(chat_id)

        if text.startswith("/start"):
            parts = text.split()
            if len(parts) == 2:
                u = db.link_telegram(parts[1], chat_id)
                if not u:
                    return "Bu davet kodu geçersiz ya da daha önce kullanılmış. Yöneticiden yeni kod iste."
                return (f"Merhaba {u['name']}, hesabın bağlandı.\n\n"
                        "Başlamak için /konum ile konumunu paylaş ya da /ev IST yaz. "
                        "Sonra /kesfet yazıp bir bölge seç.\n\n" + HELP)
            return "Davet kodunla başla: /start KOD" if not user else HELP

        if not user:
            return "Bu bot davetle çalışıyor. Yöneticiden kod al ve /start KOD yaz."

        if "location" in msg:
            loc = msg["location"]
            db.set_home(user["id"], lat=loc["latitude"], lon=loc["longitude"])
            near = self.svc.airports.cities_near_point(loc["latitude"], loc["longitude"],
                                                       user["home_radius_km"] or 150)
            if not near:
                return "Konumun kaydedildi ama yakınında havalimanı bulamadım. /yaricap 300 ile alanı genişlet."
            names = ", ".join(f"{self.svc.airports.city_name(c)} ({km:.0f} km)" for c, km in near[:3])
            return (f"Konumun kaydedildi. Kalkış için şu havalimanlarına bakacağım: {names}.",
                    {"remove_keyboard": True})

        if not text.startswith("/"):
            return "Ne yapmak istediğini komutla yaz. Örneğin /kesfet. Tüm komutlar için /yardim."
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()

        if cmd in ("/yardim", "/help"):
            return HELP
        if cmd == "/konum":
            return ("Aşağıdaki düğmeye basıp konumunu paylaş.", {
                "keyboard": [[{"text": "Konumumu paylaş", "request_location": True}]],
                "resize_keyboard": True, "one_time_keyboard": True})
        if cmd == "/ev":
            if not args:
                return "Şehir ya da havalimanı kodu yaz, örneğin: /ev IST"
            db.set_home(user["id"], code=args[0])
            return f"Ev havalimanın {self.svc.airports.city_name(args[0])} olarak kaydedildi."
        if cmd == "/yaricap":
            if not args or not args[0].isdigit():
                return "Kilometre olarak yaz, örneğin: /yaricap 200"
            db.set_home(user["id"], radius_km=float(args[0]))
            return f"Konumundan {args[0]} km içindeki havalimanlarına bakacağım."
        if cmd == "/kesfet":
            has_region = any(len(a) > 3 and find_region(a) for a in args)
            if not has_region:
                days = next((a for a in args if a.replace("-", "").isdigit()), "3-7")
                return ("Nereye bakalım?", self.region_keyboard(days))
            return self._search(chat_id, user, ["konum", *args])
        if cmd == "/ara":
            return self._search(chat_id, user, args)
        if cmd == "/bolgeler":
            from .regions import REGIONS
            return "Arama ve takipte kullanabileceğin bölgeler:\n" + "\n".join(
                f"<b>{r.key}</b>: {r.label}" + (f". {r.note}" if r.note else "") for r in REGIONS.values())
        if cmd == "/takip":
            return self._watch(user, args)
        if cmd == "/takipler":
            ws = db.watches(user["id"])
            if not ws:
                return "Henüz takibin yok. Bir arama yapıp sonuçların altındaki düğmeye bas ya da /takip IST 3-5 vizesiz 4000 yaz."
            cur = self.svc.s.currency
            from .formatting import money
            return "\n\n".join(
                f"<b>#{w['id']}</b> {w['name']}\nHedef: {money(w['threshold'], cur) if w['threshold'] else 'yok'}, "
                f"son en ucuz: {money(w['last_best'], cur) if w['last_best'] else 'henüz bakılmadı'}"
                + ("" if w["active"] else "\nTarihi geçtiği için durduruldu.") for w in ws) \
                + "\n\nSilmek için: /sil NUMARA"
        if cmd == "/sil":
            if not args or not args[0].lstrip("#").isdigit():
                return "Silinecek takibin numarasını yaz, örneğin: /sil 3"
            ok = db.delete_watch(user["id"], int(args[0].lstrip("#")))
            return "Takip silindi." if ok else "Bu numarada bir takibin yok. /takipler ile bak."
        if cmd == "/davet":
            if not user["is_admin"]:
                return "Davet oluşturmak sadece yöneticiye açık."
            name = " ".join(args).strip() or "Misafir"
            if db.count_users() >= 50:
                return "50 kullanıcı sınırı doldu."
            u = db.add_user(name)
            base = self.svc.s.base_url.rstrip("/")
            return (f"{name} için davet hazır. Aşağıdaki mesajı ona ilet:\n\n"
                    f"UcuzGez'e davetlisin. Telegram'da bota şunu yaz: <code>/start {u['tg_invite']}</code>\n"
                    f"Web'den girmek için: {base}/giris?t={u['token']}")
        if cmd == "/kullanicilar":
            if not user["is_admin"]:
                return "Bu komut sadece yöneticiye açık."
            rows = db.all_users()
            return "\n".join(f"#{r['id']} {r['name']}: "
                             + ("bağlı" if r['tg_chat_id'] else f"bekliyor, kod {r['tg_invite']}") for r in rows)
        if cmd == "/radar":
            self.send(chat_id, "Fiyatlar taranıyor…")
            r = self.svc.radar(user)
            return r or ("Şu an normalinden belirgin ucuz bir uçuş yok. Radar her rota için birkaç günlük "
                         "fiyat geçmişi biriktirdikçe daha çok fırsat yakalar.")
        return "Bu komutu tanımıyorum. Tüm komutlar için /yardim."

    def _search(self, chat_id: int, user, args: list[str]):
        ps = parse_search(args)
        ps.query.origins = self.svc.resolve_origins(user, ps.origin_text)
        names = ", ".join(self.svc.airports.city_name(o) for o in ps.query.origins)
        from .regions import REGIONS
        where = REGIONS[ps.query.region].label.lower() if ps.query.region else "her yer"
        self.send(chat_id, f"{names} çıkışlı, hedef: {where}. {ps.query.date_from:%d.%m}–{ps.query.date_to:%d.%m} "
                           f"arası başlayan {ps.query.min_days}-{ps.query.max_days} günlük geziler aranıyor, "
                           f"biraz sürebilir…")
        res = self.svc.plan(ps.query)
        self.svc.db.kv_set(f"lastq:{chat_id}", self.svc.query_to_params(ps.query, ps.rolling_days or 60))
        self.svc.db.kv_set(f"lastdays:{chat_id}", f"{ps.query.min_days}-{ps.query.max_days}")
        return (self.svc.format_result(res), self.result_keyboard())

    def _watch_name(self, q, rolling: bool) -> str:
        from .regions import REGIONS
        ap = self.svc.airports
        where = ", ".join(ap.city_name(c) for c in q.destinations) if q.destinations else \
            (REGIONS[q.region].label if q.region else "her yer")
        when = "önümüzdeki 60 gün" if rolling else f"{q.date_from:%d.%m}–{q.date_to:%d.%m}"
        return f"{', '.join(ap.city_name(o) for o in q.origins)} çıkışlı, {where}, {q.min_days}-{q.max_days} gün ({when})"

    def _watch(self, user, args: list[str]) -> str:
        if not args:
            return ("Örnekler:\n/takip IST 3-5 vizesiz 4000\n/takip IST 01.12 31.12 3-5 VIE PRG 5000\n"
                    "Tarih yazmazsan hep önümüzdeki 60 güne bakarım. Sondaki sayı hedef fiyattır.")
        ps = parse_search(args)
        ps.query.origins = self.svc.resolve_origins(user, ps.origin_text)
        threshold = ps.budget
        ps.query.budget = None
        if len(self.svc.db.watches(user["id"])) >= 10:
            return "En fazla 10 takip açabilirsin. /takipler ile bak, /sil ile yer aç."
        name = self._watch_name(ps.query, bool(ps.rolling_days))
        wid = self.svc.db.add_watch(user["id"], name,
                                    self.svc.query_to_params(ps.query, ps.rolling_days), threshold)
        return (f"Takip #{wid} açıldı: {name}.\nYaklaşık {self.svc.s.watch_interval_min // 60} saatte bir bakacağım. "
                "Fiyat hedefinin altına inerse ya da normalinden belirgin ucuzlarsa yazacağım.")
