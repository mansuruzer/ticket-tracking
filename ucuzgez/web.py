"""Web arayüzü + JSON API (Starlette). Giriş: kişiye özel link (/giris?t=TOKEN)."""
from __future__ import annotations

import secrets
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

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


def create_web_app(svc: Services, lifespan=None, extra_routes: list | None = None) -> Starlette:
    ap = svc.airports
    places = Places(ap)
    cur = svc.s.currency
    TEMPLATES.env.globals.update(
        money=lambda v: money(v, cur), day_str=day_str, short_day=short_day, airline_name=airline_name,
        leg_links=leg_links, city=ap.city_name, ablative=ablative, airline_choices=AIRLINE_CHOICES,
        quick_regions=[REGIONS[k] for k in QUICK_REGIONS], stay_presets=STAY_PRESETS, visa_date=VISA_INFO_DATE,
        provider=svc.s.provider, place_label=places.label, place_kind=places.kind_label,
        describe=places.describe, regions_all=REGIONS,
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

    def render(request, name, status=200, **ctx):
        return TEMPLATES.TemplateResponse(request, name, ctx, status_code=status)

    def defaults(user) -> dict:
        t = date.today()
        return {"origin": "", "date_from": (t + timedelta(days=1)).isoformat(),
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
        u = current_user(request)
        if not u:
            return render(request, "login.html")
        return render(request, "index.html", user=u, page="ara", form=defaults(u),
                      origins=svc.home_origins(u), res=None, q=None, error=None)

    def page_ctx(u, form, **kw):
        return dict(user=u, page="ara", form=form, origins=svc.home_origins(u), res=None, q=None,
                    error=None, trips=[], **kw)

    async def search(request: Request):
        u = current_user(request)
        if not u:
            return RedirectResponse("/", status_code=303)
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
        ctx.update(res=res, q=q, trips=[trip_view(t) for t in res.trips], where=places.describe(inc, exc),
                   visa_notes=[REGIONS[t[2:]].note for t in inc if t.startswith("r:") and REGIONS[t[2:]].note])
        return render(request, "index.html", **ctx)

    async def api_places(request: Request):
        if not current_user(request):
            return JSONResponse([], status_code=401)
        return JSONResponse(places.search(request.query_params.get("q", "")))

    async def watches(request: Request):
        u = current_user(request)
        if not u:
            return RedirectResponse("/", status_code=303)
        if request.method == "POST":
            f = await request.form()
            try:
                q, budget = form_query(u, f)
            except ValueError as e:
                return render(request, "watches.html", user=u, page="takip", items=svc.db.watches(u["id"]),
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
        return render(request, "watches.html", user=u, page="takip", items=svc.db.watches(u["id"]),
                      added=request.query_params.get("eklendi") == "1", error=None)

    async def del_watch(request: Request):
        u = current_user(request)
        if u:
            svc.db.delete_watch(u["id"], int(request.path_params["wid"]))
        return RedirectResponse("/takipler", status_code=303)

    async def settings_page(request: Request):
        u = current_user(request)
        if not u:
            return RedirectResponse("/", status_code=303)
        if request.method == "POST":
            f = await request.form()
            lat, lon = f.get("lat"), f.get("lon")
            svc.db.set_home(u["id"],
                            lat=float(lat) if lat else None, lon=float(lon) if lon else None,
                            code=(f.get("home_code") or "").strip() if "home_code" in f else None,
                            radius_km=float(f["radius"]) if f.get("radius") else None)
            if f.get("clear_location") == "1":
                with svc.db.tx() as c:
                    c.execute("UPDATE users SET home_lat=NULL, home_lon=NULL WHERE id=?", (u["id"],))
            return RedirectResponse("/ayarlar?kaydedildi=1", status_code=303)
        near = []
        if u["home_lat"] is not None:
            dist = dict(ap.cities_near_point(u["home_lat"], u["home_lon"], u["home_radius_km"] or 150))
            near = [(c, dist[c]) for c in svc.home_origins(u) if c in dist]
        return render(request, "settings.html", user=u, page="ayar", origins=svc.home_origins(u), near=near,
                      saved=request.query_params.get("kaydedildi") == "1")

    async def login(request: Request):
        tok = request.query_params.get("t", "")
        if request.method == "POST":
            tok = ((await request.form()).get("token") or "").strip()
        if not svc.db.user_by_token(tok):
            return render(request, "login.html", error="Bu giriş kodu geçerli değil. Kodu yöneticiden tekrar iste.",
                          status=401)
        r = RedirectResponse("/", status_code=303)
        r.set_cookie(COOKIE, tok, max_age=180 * 86400, httponly=True, samesite="lax")
        return r

    async def logout(request: Request):
        r = RedirectResponse("/", status_code=303)
        r.delete_cookie(COOKIE)
        return r

    # ---- JSON API -------------------------------------------------------------------
    async def api_search(request: Request):
        u = current_user(request)
        if not u:
            return JSONResponse({"error": "yetkisiz"}, status_code=401)
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

    async def legacy_redirect(request: Request):
        return RedirectResponse("/ayarlar" if request.url.path == "/ev" else "/takipler", status_code=307)

    return Starlette(routes=[
        Route("/", index),
        Route("/ara", search, methods=["GET", "POST"]),
        Route("/takipler", watches, methods=["GET", "POST"]),
        Route("/takipler/{wid:int}/sil", del_watch, methods=["POST"]),
        Route("/ayarlar", settings_page, methods=["GET", "POST"]),
        Route("/giris", login, methods=["GET", "POST"]),
        Route("/cikis", logout),
        Route("/takip", legacy_redirect, methods=["POST"]),
        Route("/ev", legacy_redirect, methods=["POST"]),
        Route("/api/ara", api_search),
        Route("/api/yerler", api_places),
        Route("/saglik", health),
        *(extra_routes or []),
    ], lifespan=lifespan)
