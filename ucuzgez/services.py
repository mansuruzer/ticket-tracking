"""Uygulamanın çekirdeği: arama, takip kontrolü ve fırsat radarı.

Web, Telegram ve zamanlayıcı yalnızca bu sınıfı kullanır.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from datetime import date, datetime, timedelta

from .config import Settings
from .db import DB
from .formatting import airline_name, day_str, money, trip_telegram, google_flights_link
from .geo import get_airports
from .ground import GroundEstimator
from .planner import Planner, PlanResult, TripQuery
from .providers import build_search, build_verifiers

log = logging.getLogger(__name__)


class Services:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = DB(settings.db_path, settings.turso_url, settings.turso_token)
        self.airports = get_airports()
        self.search = build_search(settings, self.airports, self.db)
        self.ground = GroundEstimator(self.airports, settings.ground_base_eur, settings.ground_eur_per_km)
        self.planner = Planner(self.search, self.airports, self.ground, settings.eur_rate,
                               build_verifiers(settings, self.airports), settings.verify_top_n)
        self._lock = threading.Lock()   # aynı anda tek ağır tarama (API limitleri için)

    # ---- kalkış noktası ------------------------------------------------------
    def home_origins(self, user) -> list[str]:
        """Kullanıcının konumuna göre yakın şehirler (en fazla 3) ya da kayıtlı kodu."""
        if user["home_lat"] is not None:
            near = self.airports.cities_near_point(user["home_lat"], user["home_lon"],
                                                   user["home_radius_km"] or 150)
            if near:
                # büyük havalimanları öncelikli; yakında hiç yoksa en yakın küçükler
                big = [c for c, _ in near if c in self.airports.popular]
                return (big or [c for c, _ in near])[:3]
        if user["home_code"]:
            return [self.airports.city_of(user["home_code"])]
        return []

    def resolve_origins(self, user, text: str | None) -> list[str]:
        if not text or text.lower() in ("konum", "ev", "home", "-"):
            o = self.home_origins(user) if user else []
            if not o:
                raise ValueError("Kalkış yok: şehir kodu yaz (örn IST) ya da önce konumunu kaydet.")
            return o
        return [self.airports.city_of(x.strip()) for x in text.replace(" ", ",").split(",") if x.strip()]

    # ---- sorgu <-> JSON --------------------------------------------------------
    @staticmethod
    def query_to_params(q: TripQuery, rolling_days: int | None = None) -> dict:
        d = asdict(q)
        for k in ("airlines", "exclude_airlines", "exclude_countries", "include_countries"):
            d[k] = sorted(d[k]) if d[k] is not None else None
        if rolling_days:
            d["rolling"] = {"start": max(1, (q.date_from - date.today()).days), "days": rolling_days}
        d["date_from"], d["date_to"] = q.date_from.isoformat(), q.date_to.isoformat()
        return d

    @staticmethod
    def params_to_query(p: dict) -> TripQuery:
        p = dict(p)
        roll = p.pop("rolling", None)
        if roll:
            start = date.today() + timedelta(days=roll["start"])
            p["date_from"], p["date_to"] = start, start + timedelta(days=roll["days"])
        else:
            p["date_from"], p["date_to"] = date.fromisoformat(p["date_from"]), date.fromisoformat(p["date_to"])
            today = date.today() + timedelta(days=1)
            if p["date_from"] < today:
                p["date_from"] = today
        for k in ("airlines", "exclude_airlines", "exclude_countries", "include_countries"):
            p[k] = set(p[k]) if p.get(k) is not None else None
        return TripQuery(**p)

    # ---- arama -----------------------------------------------------------------
    def plan(self, q: TripQuery) -> PlanResult:
        with self._lock:
            return self.planner.plan(q)

    def format_result(self, res: PlanResult, n: int = 5) -> str:
        if not res.trips:
            return ("Bu ölçütlere uyan gezi çıkmadı. Tarihleri genişletmeyi, gün sayısını esnetmeyi "
                    "ya da başka bölge seçmeyi dene. " + " ".join(res.notes)).strip()
        parts = [trip_telegram(t, self.airports, self.s.currency, i) for i, t in enumerate(res.trips[:n], 1)]
        foot = f"\n<i>{res.destinations_scanned} şehir tarandı."
        if self.s.provider == "travelpayouts":
            foot += " Fiyatlar son 48 saatte görülen fiyatlar, almadan önce linkten kontrol et."
        if self.s.provider == "mock":
            foot += " Şu an deneme verisi, fiyatlar gerçek değil."
        foot += "</i>"
        if res.notes:
            foot += "\n" + " ".join(res.notes)
        return "\n\n".join(parts) + foot

    # ---- takip (alarm) -------------------------------------------------------------
    def check_watch(self, w) -> str | None:
        """Takibi çalıştırır; haber verilecek bir şey varsa mesaj döner."""
        import json
        try:
            q = self.params_to_query(json.loads(w["params"]))
            if q.date_to < date.today():
                with self.db.tx() as c:
                    c.execute("UPDATE watches SET active=0 WHERE id=?", (w["id"],))
                return f"Takip #{w['id']} ({w['name']}) tarihi geçtiği için durduruldu."
            res = self.plan(q)
        except Exception as e:  # tek takip hatası diğerlerini durdurmasın
            log.exception("takip %s hata", w["id"])
            self.db.watch_checked(w["id"], None)
            return None
        best = res.trips[0] if res.trips else None
        median, n = self.db.watch_median(w["id"])
        self.db.watch_checked(w["id"], best.total if best else None)
        if not best:
            return None

        reasons = []
        if w["threshold"] and best.total <= w["threshold"]:
            reasons.append(f"hedefin ({money(w['threshold'], self.s.currency)}) altında")
        if median and n >= 3 and best.total <= median * 0.85:
            reasons.append(f"son 30 günün normalinden %{(1 - best.total / median) * 100:.0f} ucuz")
        if not reasons:
            return None
        last = self.db.last_notified(w["user_id"], "watch", str(w["id"]), 7 * 86400)
        if last and best.total >= last["price"] * 0.97:
            return None     # zaten haber verdik, belirgin yeni düşüş yok
        self.db.mark_notified(w["user_id"], "watch", str(w["id"]), best.total)
        return (f"<b>Fiyat düştü: takip #{w['id']}</b>\n{w['name']}\nEn ucuz gezi " + " ve ".join(reasons) + ".\n\n"
                + self.format_result(PlanResult(res.trips[:3], res.api_calls, res.cache_hits,
                                                res.destinations_scanned, []), 3))

    # ---- fırsat radarı -------------------------------------------------------------
    def radar(self, user, days_ahead: int = 90, max_deals: int = 3) -> str | None:
        """Evden her yere tek yön fiyatları tarar; rotanın normalinden çok ucuz olanları bildirir."""
        origins = self.home_origins(user)
        if not origins:
            return None
        d1, d2 = date.today() + timedelta(days=3), date.today() + timedelta(days=days_ahead)
        best: dict[tuple[str, str], object] = {}
        with self._lock:
            for o in origins:
                for f in self.search.search_oneway(o, None, d1, d2, max_transfers=1):
                    k = (f"{f.origin_city}-{f.destination_city}", f.depart.isoformat())
                    if k not in best or f.price < best[k].price:
                        best[k] = f
        if not best:
            return None
        # rota başına en ucuz gün
        per_route: dict[str, object] = {}
        for (route, _), f in best.items():
            if route not in per_route or f.price < per_route[route].price:
                per_route[route] = f
        medians = {r: self.db.route_median(r) for r in per_route}
        self.db.record_prices([(r, d, f.price) for (r, d), f in best.items()])

        deals = []
        for route, f in per_route.items():
            med, n = medians[route]
            if med and n >= 5 and f.price <= med * 0.65:
                ref = f"{route}@{f.depart}"
                if self.db.last_notified(user["id"], "radar", ref, 3 * 86400):
                    continue
                deals.append((f.price / med, route, f, med))
        if not deals:
            return None
        deals.sort(key=lambda x: x[0])
        lines = ["<b>Fırsat radarı</b>: normalinden çok ucuz tek yön uçuşlar"]
        for ratio, route, f, med in deals[:max_deals]:
            self.db.mark_notified(user["id"], "radar", f"{route}@{f.depart}", f.price)
            lines.append(
                f"- {self.airports.city_name(f.origin_city)} → {self.airports.city_name(f.destination_city)} "
                f"{day_str(f.depart)} {airline_name(f.airline)} <b>{money(f.price, self.s.currency)}</b> "
                f"(normali ~{money(med, self.s.currency)}, %{(1 - ratio) * 100:.0f} ucuz) "
                f"<a href=\"{f.link or google_flights_link(f.origin, f.destination, f.depart)}\">bak</a>")
        lines.append("Dönüşüyle birlikte planlamak için /ara ile o şehri ara.")
        return "\n".join(lines)
