"""Web arayüzü + JSON API (Starlette). Giriş: kişiye özel link (/giris?t=TOKEN)."""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from .formatting import ablative, airline_name, day_str, leg_links, money, short_day
from .commands import build_query_targets
from .places import Places
from .planner import Trip, TripQuery
from .regions import REGIONS, VISA_INFO_DATE
from .services import Services

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
COOKIE = "ug_t"
AIRLINE_CHOICES = [("PC", "Pegasus"), ("VF", "AJet"), ("TK", "THY"), ("W6", "Wizz Air"),
                   ("FR", "Ryanair"), ("U2", "easyJet"), ("XQ", "SunExpress")]
QUICK_REGIONS = ["vizesiz", "schengen", "balkanlar", "kafkasya", "ortadogu", "yurtici", "kolay-vize", "avrupa"]
STAY_PRESETS = [("2", "3", "Hafta sonu"), ("3", "5", "3–5 gün"), ("5", "8", "Bir hafta"), ("9", "14", "Uzun")]
DATE_PRESETS = [("30", "30 gün"), ("next", "Gelecek ay"), ("60", "2 ay"), ("90", "3 ay")]
TINTS = ["lav", "mint", "peach", "sky", "butter", "rose"]
TR_MONTHS_LONG = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
                  "Kasım", "Aralık"]
STATIC = Path(__file__).parent / "static"


def tint(key: str) -> str:
    """Şehre göre sabit pastel ton (her şehir hep aynı renkte görünsün)."""
    return TINTS[sum(map(ord, key or "")) % len(TINTS)]


def price_calendar(daily: dict, d1: date, d2: date) -> list[dict]:
    """Gidiş penceresini ay ay takvim haftalarına böler; her güne 0-4 arası ucuzluk seviyesi verir."""
    if not daily:
        return []
    prices = sorted(p for p, _ in daily.values())
    lo, hi = prices[0], prices[-1]

    def level(p: float) -> int:
        return 0 if hi == lo else min(4, int((p - lo) / (hi - lo) * 5))

    months, d = [], d1.replace(day=1)
    while d <= d2:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        cells: list[dict | None] = [None] * d.weekday()
        day = d
        while day < nxt:
            info = daily.get(day)
            cells.append({"d": day, "in": d1 <= day <= d2, "price": info[0] if info else None,
                          "city": info[1] if info else None, "lvl": level(info[0]) if info else None,
                          "best": bool(info) and info[0] == lo})
            day += timedelta(days=1)
        cells += [None] * (-len(cells) % 7)
        if any(c and c["in"] for c in cells):
            weeks = [cells[i:i + 7] for i in range(0, len(cells), 7)]
            months.append({"label": f"{TR_MONTHS_LONG[d.month - 1]} {d.year}",     # aralık dışı haftalar gizli
                           "weeks": [w for w in weeks if any(c and c["in"] for c in w)]})
        d = nxt
    return months


def sparkline(series: list[tuple[float, float]], w: int = 144, h: int = 40) -> dict | None:
    """Takip fiyat geçmişi için küçük SVG çizgisi."""
    if len(series) < 2:
        return None
    t0, t1 = series[0][0], series[-1][0]
    ps = [p for _, p in series]
    lo, hi = min(ps), max(ps)
    pad = 4

    def xy(t, p):
        x = pad + (t - t0) / ((t1 - t0) or 1) * (w - 2 * pad)
        y = pad + (1 - (p - lo) / ((hi - lo) or 1)) * (h - 2 * pad)
        return round(x, 1), round(y, 1)

    pts = [xy(t, p) for t, p in series]
    line = "M" + " L".join(f"{x},{y}" for x, y in pts)
    area = f"{line} L{pts[-1][0]},{h} L{pts[0][0]},{h} Z"
    return {"line": line, "area": area, "last": pts[-1], "w": w, "h": h, "lo": lo, "hi": hi,
            "down": ps[-1] <= ps[0]}


def create_web_app(svc: Services, lifespan=None, extra_routes: list | None = None) -> Starlette:
    ap = svc.airports
    places = Places(ap)
    cur = svc.s.currency
    TEMPLATES.env.globals.update(
        money=lambda v: money(v, cur), day_str=day_str, short_day=short_day, airline_name=airline_name,
        leg_links=leg_links, city=ap.city_name, ablative=ablative, airline_choices=AIRLINE_CHOICES,
        quick_regions=[REGIONS[k] for k in QUICK_REGIONS], stay_presets=STAY_PRESETS, visa_date=VISA_INFO_DATE,
        provider=svc.s.provider, place_label=places.label, place_kind=places.kind_label,
        describe=places.describe, regions_all=REGIONS, date_presets=DATE_PRESETS, tint=tint,
        country=lambda c: places.country_name(ap.country_of(c)),
    )
    TEMPLATES.env.filters["city"] = ap.city_name

    def ts_str(ts: float) -> str:
        from .scheduler import local_now  # noqa: F401
        from datetime import datetime, timezone
        d = datetime.fromtimestamp(ts, timezone(timedelta(hours=3)))
        return f"{short_day(d.date())} {d:%H:%M}"
    TEMPLATES.env.globals["ts_str"] = ts_str

    def current_user(request: Request):
        tok = request.cookies.get(COOKIE) or request.headers.get("X-Token", "")
        u = svc.db.user_by_token(tok)
        return u if u and secrets.compare_digest(u["token"], tok) else None

    # Site herkese açık: linki olan herkes arama yapar. Girişsiz ziyaretçi ANON'dur ve veritabanına
    # yazılmaz; ayar kaydedince bu tarayıcı için bir misafir hesabı açılır. Takip ve Telegram yalnız
    # yönetici ya da Telegram'ı bağlı kullanıcı içindir.
    ANON = {"id": None, "name": "Misafir", "token": "", "tg_invite": "", "tg_chat_id": None, "home_lat": None,
            "home_lon": None, "home_radius_km": 150, "home_code": None, "is_admin": 0}

    def viewer(request: Request):
        return current_user(request) or ANON

    def can_track(u) -> bool:
        return u is not None and u is not ANON and bool(u["is_admin"] or u["tg_chat_id"])

    def render(request, name, status=200, **ctx):
        u = ctx.get("user")
        ctx.setdefault("track", can_track(u))
        ctx.setdefault("guest", u is None or u is ANON)
        return TEMPLATES.TemplateResponse(request, name, ctx, status_code=status)

    def defaults(user) -> dict:
        t = date.today()
        return {"origin": "" if user and svc.home_origins(user) else "IST", "date_from": (t + timedelta(days=1)).isoformat(),
                "date_to": (t + timedelta(days=45)).isoformat(), "min_days": "3", "max_days": "5",
                "to": [], "not": [], "budget": "", "max_ground_hours": "6", "airlines": []}

    def targets_from(f) -> tuple[list[str], list[str]]:
        inc = [t for t in f.getlist("to") if places.valid(t)]
        exc = [t for t in f.getlist("not") if places.valid(t)]
        if f.get("region"):                      # eski linkler / takipler
            inc.append(f"r:{f.get('region')}")
        for c in (f.get("destinations") or "").replace(" ", ",").split(","):
            if c.strip():
                inc.append(f"y:{ap.city_of(c.strip().upper())}")
        inc = [t for t in dict.fromkeys(inc) if t != "r:her-yer"]
        return inc, list(dict.fromkeys(exc))

    # ---- form -> sorgu ---------------------------------------------------------
    def form_query(user, f) -> tuple[TripQuery, float | None]:
        origins = svc.resolve_origins(user, (f.get("origin") or "").strip() or None)
        today = date.today()
        try:
            d1 = date.fromisoformat(f.get("date_from") or (today + timedelta(days=1)).isoformat())
            d2 = date.fromisoformat(f.get("date_to") or (d1 + timedelta(days=60)).isoformat())
            mn, mx = int(f.get("min_days") or 3), int(f.get("max_days") or 7)
            budget = float(f["budget"]) if f.get("budget") else None
            ground = float(f.get("max_ground_hours") or svc.s.max_ground_hours)
        except ValueError:
            raise ValueError("Tarih, gün ya da bütçe alanında geçersiz bir değer var.")
        if d1 < today:
            raise ValueError("En erken gidiş tarihi bugünden önce olamaz.")
        airlines = set(f.getlist("airlines")) if hasattr(f, "getlist") else set()
        inc, exc = targets_from(f)
        q = TripQuery(
            origins=origins, date_from=d1, date_to=d2, min_days=min(mn, mx), max_days=max(mn, mx),
            budget=budget, open_jaw=f.get("same_city") != "on", max_transfers=0 if f.get("direct") == "on" else 1,
            max_ground_hours=ground, airlines=airlines or None,
        )
        build_query_targets(q, inc, exc)
        q.validate()
        return q, budget

    def trip_view(t: Trip) -> dict:
        return {"t": t, "total": t.total, "date": t.outbound.depart.isoformat(), "days": t.days}

    # ---- sayfalar ----------------------------------------------------------------
    async def index(request: Request):
        u = viewer(request)
        return render(request, "index.html", user=u, page="ara", form=defaults(u),
                      origins=svc.home_origins(u), res=None, q=None, error=None)

    def page_ctx(u, form, **kw):
        return dict(user=u, page="ara", form=form, origins=svc.home_origins(u), res=None, q=None,
                    error=None, trips=[], **kw)

    async def search(request: Request):
        u = viewer(request)
        f = request.query_params if request.method == "GET" else await request.form()
        inc, exc = targets_from(f)
        form = {**defaults(u), **{k: f.get(k) for k in f.keys()}, "to": inc, "not": exc,
                "airlines": f.getlist("airlines")}
        try:
            q, _ = form_query(u, f)
        except ValueError as e:
            return render(request, "index.html", status=400, **{**page_ctx(u, form), "error": str(e)})
        res = await run_in_threadpool(svc.plan, q)
        ctx = page_ctx(u, form)
        base = [(k, v) for k, v in f.multi_items() if k not in ("date_from", "date_to")]
        ctx.update(calendar=price_calendar(res.daily, q.date_from, q.date_to),
                   day_url=lambda d: "/ara?" + urlencode(base + [("date_from", d.isoformat()), ("date_to", d.isoformat())]),
                   share_url=str(request.url) if request.method == "GET" else "",
                   single_day=q.date_from == q.date_to)
        ctx.update(res=res, q=q, trips=[trip_view(t) for t in res.trips], where=places.describe(inc, exc),
                   visa_notes=[REGIONS[t[2:]].note for t in inc if t.startswith("r:") and REGIONS[t[2:]].note])
        return render(request, "index.html", **ctx)

    async def api_places(request: Request):
        return JSONResponse(places.search(request.query_params.get("q", "")))

    async def watches(request: Request):
        u = current_user(request)
        if not can_track(u):
            return RedirectResponse("/", status_code=303)
        if request.method == "POST":
            f = await request.form()
            try:
                q, budget = form_query(u, f)
            except ValueError as e:
                return render(request, "watches.html", user=u, page="takip", items=watch_items(u),
                              error=str(e), status=400)
            q.budget = None
            rolling = 60 if f.get("rolling") == "on" else None
            inc, exc = targets_from(f)
            when = "önümüzdeki 60 gün" if rolling else f"{short_day(q.date_from)}–{short_day(q.date_to)}"
            name = (f.get("name") or "").strip() or \
                f"{', '.join(ap.city_name(o) for o in q.origins)} çıkışlı, {places.describe(inc, exc)}, " \
                f"{q.min_days}-{q.max_days} gün ({when})"
            params = svc.query_to_params(q, rolling)
            params["_targets"] = {"include": inc, "exclude": exc}
            svc.db.add_watch(u["id"], name, params, budget)
            return RedirectResponse("/takipler?eklendi=1", status_code=303)
        return render(request, "watches.html", user=u, page="takip", items=watch_items(u),
                      added=request.query_params.get("eklendi") == "1", error=None)

    def watch_search_url(params: dict) -> str | None:
        try:
            q = svc.params_to_query(params)
        except Exception:  # noqa: BLE001 - eski/bozuk kayıt: link verme
            return None
        tg = params.get("_targets") or {}
        qs: list[tuple[str, str]] = [("origin", ",".join(q.origins)), ("date_from", q.date_from.isoformat()),
                                     ("date_to", max(q.date_from, q.date_to).isoformat()),
                                     ("min_days", str(q.min_days)), ("max_days", str(q.max_days)),
                                     ("max_ground_hours", f"{q.max_ground_hours:g}")]
        qs += [("to", t) for t in tg.get("include", [])] + [("not", t) for t in tg.get("exclude", [])]
        qs += [("airlines", a) for a in sorted(q.airlines or [])]
        if not q.open_jaw:
            qs.append(("same_city", "on"))
        if q.max_transfers == 0:
            qs.append(("direct", "on"))
        return "/ara?" + urlencode(qs)

    def watch_items(u) -> list[dict]:
        out = []
        for w in svc.db.watches(u["id"]):
            params = json.loads(w["params"])
            series = svc.db.watch_series(w["id"])
            out.append({"w": w, "spark": sparkline(series), "url": watch_search_url(params),
                        "low": min((p for _, p in series), default=None), "n": len(series),
                        "rolling": bool(params.get("rolling"))})
        return out

    async def toggle_watch(request: Request):
        u = current_user(request)
        if u:
            f = await request.form()
            svc.db.set_watch_active(u["id"], int(request.path_params["wid"]), f.get("active") == "1")
        return RedirectResponse("/takipler", status_code=303)

    async def del_watch(request: Request):
        u = current_user(request)
        if u:
            svc.db.delete_watch(u["id"], int(request.path_params["wid"]))
        return RedirectResponse("/takipler", status_code=303)

    async def settings_page(request: Request):
        u = viewer(request)
        if request.method == "POST":
            f = await request.form()
            new_cookie = None
            if u is ANON:                     # ilk kayıtta bu tarayıcıya misafir hesabı aç
                u = svc.db.add_user("Misafir")
                new_cookie = u["token"]
            lat, lon = f.get("lat"), f.get("lon")
            svc.db.set_home(u["id"],
                            lat=float(lat) if lat else None, lon=float(lon) if lon else None,
                            code=(f.get("home_code") or "").strip() if "home_code" in f else None,
                            radius_km=float(f["radius"]) if f.get("radius") else None)
            if f.get("clear_location") == "1":
                with svc.db.tx() as c:
                    c.execute("UPDATE users SET home_lat=NULL, home_lon=NULL WHERE id=?", (u["id"],))
            r = RedirectResponse("/ayarlar?kaydedildi=1", status_code=303)
            if new_cookie:
                r.set_cookie(COOKIE, new_cookie, max_age=365 * 86400, httponly=True, samesite="lax")
            return r
        near = []
        if u["home_lat"] is not None:
            dist = dict(ap.cities_near_point(u["home_lat"], u["home_lon"], u["home_radius_km"] or 150))
            near = [(c, dist[c]) for c in svc.home_origins(u) if c in dist]
        return render(request, "settings.html", user=u, page="ayar", origins=svc.home_origins(u), near=near,
                      saved=request.query_params.get("kaydedildi") == "1")

    async def login(request: Request):
        raw = request.query_params.get("t", "")
        if request.method == "POST":
            raw = (await request.form()).get("token") or ""
        u = find_login_user(raw)
        if not u:
            return render(request, "login.html", status=401,
                          error="Bu kod geçerli değil. Sana gönderilen linkin tamamını ya da Telegram kodunu "
                                "(örn. /start 1a2b3c4d) buraya yapıştırabilirsin.")
        r = RedirectResponse("/", status_code=303)
        r.set_cookie(COOKIE, u["token"], max_age=180 * 86400, httponly=True, samesite="lax")
        return r

    def find_login_user(raw: str):
        """Giriş linkinin tamamını, linkteki kodu ya da Telegram davet kodunu kabul eder."""
        s = (raw or "").strip().strip("\"'<>").rstrip(".,;:!)")
        if "t=" in s:                                       # link olduğu gibi yapıştırıldıysa
            s = (parse_qs(urlparse(s).query).get("t") or parse_qs(s.split("?", 1)[-1]).get("t") or [s])[0]
        s = re.sub(r"^/?start\s+", "", s, flags=re.I).strip()
        if not s:
            return None
        for cand in dict.fromkeys([s, s[:1].lower() + s[1:], s[:1].upper() + s[1:]]):   # telefon ilk harfi büyütmüş olabilir
            u = svc.db.user_by_token(cand)
            if u:
                return u
        if re.fullmatch(r"[0-9a-fA-F]{8}", s):              # Telegram /start kodu
            return svc.db.user_by_invite(s.lower())
        return None

    async def logout(request: Request):
        r = RedirectResponse("/", status_code=303)
        r.delete_cookie(COOKIE)
        return r

    # ---- JSON API -------------------------------------------------------------------
    async def api_search(request: Request):
        u = viewer(request)
        try:
            q, _ = form_query(u, request.query_params)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        res = await run_in_threadpool(svc.plan, q)
        return JSONResponse({
            "currency": cur, "api_calls": res.api_calls, "notes": res.notes,
            "trips": [{
                "total": t.total, "days": t.days, "open_jaw": t.open_jaw, "saving": t.saving,
                "verified": t.verified, "ground": None if not t.ground else {
                    "from": t.ground.from_city, "to": t.ground.to_city, "hours": t.ground.hours,
                    "cost": t.ground_cost, "estimated": True},
                "outbound": t.outbound.to_json(), "inbound": t.inbound.to_json(),
            } for t in res.trips]})

    async def health(_):
        return Response("ok")

    async def robots(_):                      # link herkese açık ama arama motorlarında görünmesin
        return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")

    # ---- ana ekrana ekle (PWA) -------------------------------------------------------
    async def manifest(_):
        return JSONResponse({
            "name": "UcuzGez", "short_name": "UcuzGez", "lang": "tr", "start_url": "/", "scope": "/",
            "display": "standalone", "background_color": "#F6F5FB", "theme_color": "#F6F5FB",
            "description": "Esnek tarihli ucuz gezi bulucu",
            "icons": [{"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
                      {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    def static_file(name: str, media: str):
        async def handler(_):
            p = STATIC / name
            if not p.exists():
                return Response(status_code=404)
            return Response(p.read_bytes(), media_type=media, headers={"Cache-Control": "public, max-age=604800"})
        return handler

    async def legacy_redirect(request: Request):
        return RedirectResponse("/ayarlar" if request.url.path == "/ev" else "/takipler", status_code=307)

    return Starlette(routes=[
        Route("/", index),
        Route("/ara", search, methods=["GET", "POST"]),
        Route("/takipler", watches, methods=["GET", "POST"]),
        Route("/takipler/{wid:int}/sil", del_watch, methods=["POST"]),
        Route("/takipler/{wid:int}/durum", toggle_watch, methods=["POST"]),
        Route("/ayarlar", settings_page, methods=["GET", "POST"]),
        Route("/giris", login, methods=["GET", "POST"]),
        Route("/cikis", logout),
        Route("/takip", legacy_redirect, methods=["POST"]),
        Route("/ev", legacy_redirect, methods=["POST"]),
        Route("/api/ara", api_search),
        Route("/api/yerler", api_places),
        Route("/saglik", health),
        Route("/robots.txt", robots),
        Route("/manifest.webmanifest", manifest),
        Route("/icon.svg", static_file("icon.svg", "image/svg+xml")),
        Route("/fonts/dg-latin.woff2", static_file("dg-latin.woff2", "font/woff2")),
        Route("/fonts/dg-latin-ext.woff2", static_file("dg-latin-ext.woff2", "font/woff2")),
        Route("/icon-180.png", static_file("icon-180.png", "image/png")),
        Route("/icon-512.png", static_file("icon-512.png", "image/png")),
        *(extra_routes or []),
    ], lifespan=lifespan)
