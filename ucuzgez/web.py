"""Web arayüzü + JSON API (Starlette). Giriş: kişiye özel link (/giris?t=TOKEN)."""
from __future__ import annotations

import secrets
from datetime import date, timedelta
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from .formatting import ablative, airline_name, day_str, leg_links, money
from .planner import TripQuery
from .services import Services

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
COOKIE = "ug_t"
AIRLINE_CHOICES = [("PC", "Pegasus"), ("VF", "AJet"), ("TK", "THY"), ("W6", "Wizz Air"),
                   ("FR", "Ryanair"), ("U2", "easyJet"), ("XQ", "SunExpress")]


def create_web_app(svc: Services, lifespan=None, extra_routes: list | None = None) -> Starlette:
    ap = svc.airports
    cur = svc.s.currency
    TEMPLATES.env.globals.update(
        money=lambda v: money(v, cur), day_str=day_str, airline_name=airline_name,
        leg_links=leg_links, city=ap.city_name, ablative=ablative, airline_choices=AIRLINE_CHOICES,
        provider=svc.s.provider,
    )
    TEMPLATES.env.filters["city"] = ap.city_name

    def current_user(request: Request):
        tok = request.cookies.get(COOKIE) or request.headers.get("X-Token", "")
        u = svc.db.user_by_token(tok)
        return u if u and secrets.compare_digest(u["token"], tok) else None

    def render(request, name, **ctx):
        return TEMPLATES.TemplateResponse(request, name, ctx)

    # ---- form -> sorgu ---------------------------------------------------------
    def form_query(user, f) -> tuple[TripQuery, float | None]:
        origins = svc.resolve_origins(user, (f.get("origin") or "").strip() or None)
        today = date.today()
        d1 = date.fromisoformat(f.get("date_from") or (today + timedelta(days=1)).isoformat())
        d2 = date.fromisoformat(f.get("date_to") or (d1 + timedelta(days=60)).isoformat())
        dests = [x.strip().upper() for x in (f.get("destinations") or "").replace(" ", ",").split(",") if x.strip()]
        budget = float(f["budget"]) if f.get("budget") else None
        airlines = set(f.getlist("airlines")) if hasattr(f, "getlist") else set()
        q = TripQuery(
            origins=origins, date_from=d1, date_to=d2,
            min_days=int(f.get("min_days") or 3), max_days=int(f.get("max_days") or 7),
            destinations=dests or None, budget=budget,
            open_jaw=f.get("open_jaw") == "on", max_transfers=0 if f.get("direct") == "on" else 1,
            max_ground_hours=float(f.get("max_ground_hours") or svc.s.max_ground_hours),
            airlines=airlines or None,
            exclude_countries={"TR"} if f.get("abroad") == "on" else None,
        )
        q.validate()
        return q, budget

    # ---- sayfalar ----------------------------------------------------------------
    async def index(request: Request):
        u = current_user(request)
        if not u:
            return render(request, "login.html")
        return render(request, "index.html", user=u, watches=svc.db.watches(u["id"]),
                      origins=svc.home_origins(u), today=date.today(), timedelta=timedelta)

    async def login(request: Request):
        tok = request.query_params.get("t", "")
        if request.method == "POST":
            tok = (await request.form()).get("token", "")
        if not svc.db.user_by_token(tok):
            return render(request, "login.html", error="Geçersiz giriş kodu.")
        r = RedirectResponse("/", status_code=303)
        r.set_cookie(COOKIE, tok, max_age=180 * 86400, httponly=True, samesite="lax")
        return r

    async def logout(request: Request):
        r = RedirectResponse("/", status_code=303)
        r.delete_cookie(COOKIE)
        return r

    async def search(request: Request):
        u = current_user(request)
        if not u:
            return RedirectResponse("/", status_code=303)
        f = await request.form()
        try:
            q, _ = form_query(u, f)
        except ValueError as e:
            return render(request, "results.html", user=u, error=str(e), res=None, q=None)
        res = await run_in_threadpool(svc.plan, q)
        return render(request, "results.html", user=u, res=res, q=q, error=None, form=dict(f))

    async def add_watch(request: Request):
        u = current_user(request)
        if not u:
            return RedirectResponse("/", status_code=303)
        f = await request.form()
        try:
            q, budget = form_query(u, f)
        except ValueError as e:
            return render(request, "results.html", user=u, error=str(e), res=None, q=None)
        q.budget = None
        rolling = int(f["rolling"]) if f.get("rolling") else None
        dest = ",".join(q.destinations) if q.destinations else "her yer"
        name = f.get("name") or f"{'/'.join(q.origins)}→{dest} {q.min_days}-{q.max_days}g"
        svc.db.add_watch(u["id"], name, svc.query_to_params(q, rolling), budget)
        return RedirectResponse("/#takipler", status_code=303)

    async def del_watch(request: Request):
        u = current_user(request)
        if u:
            svc.db.delete_watch(u["id"], int(request.path_params["wid"]))
        return RedirectResponse("/#takipler", status_code=303)

    async def set_home(request: Request):
        u = current_user(request)
        if not u:
            return RedirectResponse("/", status_code=303)
        f = await request.form()
        lat, lon = f.get("lat"), f.get("lon")
        svc.db.set_home(u["id"],
                        lat=float(lat) if lat else None, lon=float(lon) if lon else None,
                        code=(f.get("home_code") or "").strip() if "home_code" in f else None,
                        radius_km=float(f["radius"]) if f.get("radius") else None)
        return RedirectResponse("/", status_code=303)

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

    return Starlette(routes=[
        Route("/", index),
        Route("/giris", login, methods=["GET", "POST"]),
        Route("/cikis", logout),
        Route("/ara", search, methods=["POST"]),
        Route("/takip", add_watch, methods=["POST"]),
        Route("/takip/{wid:int}/sil", del_watch, methods=["POST"]),
        Route("/ev", set_home, methods=["POST"]),
        Route("/api/ara", api_search),
        Route("/saglik", health),
        *(extra_routes or []),
    ], lifespan=lifespan)
