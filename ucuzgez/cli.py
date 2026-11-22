"""Yönetim komutları.

  python -m ucuzgez.cli add-user "Ahmet" [--admin]
  python -m ucuzgez.cli users
  python -m ucuzgez.cli search IST 01.12 20.12 3-5          # terminalden arama
  python -m ucuzgez.cli check-watches                         # takipleri şimdi çalıştır
  python -m ucuzgez.cli update-airports                       # tüm dünya havalimanlarını indir
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from .config import load_settings

MAX_USERS = 50


def _plain(html_text: str) -> str:
    t = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2", html_text)
    return re.sub(r"</?[bi]>", "", t)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ucuzgez")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-user"); a.add_argument("name"); a.add_argument("--admin", action="store_true")
    sub.add_parser("users")
    s = sub.add_parser("search"); s.add_argument("args", nargs="+")
    sub.add_parser("check-watches")
    sub.add_parser("update-airports")
    args = ap.parse_args(argv)
    settings = load_settings()

    if args.cmd == "update-airports":
        return update_airports()

    from .services import Services
    svc = Services(settings)

    if args.cmd == "add-user":
        if svc.db.count_users() >= MAX_USERS:
            print(f"Kullanıcı sınırı ({MAX_USERS}) doldu.")
            return 1
        u = svc.db.add_user(args.name, args.admin)
        print(f"Kullanıcı #{u['id']} {u['name']} oluşturuldu")
        print(f"  Web girişi : {settings.base_url}/giris?t={u['token']}")
        print(f"  Telegram   : /start {u['tg_invite']}")
    elif args.cmd == "users":
        for u in svc.db.all_users():
            print(f"#{u['id']:<3} {u['name']:<20} tg={'✓' if u['tg_chat_id'] else '-'} "
                  f"ev={u['home_code'] or ''} konum={'✓' if u['home_lat'] else '-'} davet={u['tg_invite']}")
    elif args.cmd == "search":
        from .commands import parse_search
        ps = parse_search(args.args)
        if not ps.origin_text or ps.origin_text == "konum":
            print("Terminal aramasında kalkış kodu ver (örn IST).")
            return 1
        ps.query.origins = svc.resolve_origins(None, ps.origin_text)
        print(_plain(svc.format_result(svc.plan(ps.query), n=10)))
    elif args.cmd == "check-watches":
        for w in svc.db.due_watches(0):
            msg = svc.check_watch(w)
            print(f"#{w['id']} {w['name']}: " + (_plain(msg) if msg else "bildirim yok"))
    return 0


def update_airports() -> int:
    import requests
    from .geo import FULL_FILE
    base = "https://api.travelpayouts.com/data"
    print("İndiriliyor…")
    airports = requests.get(f"{base}/en/airports.json", timeout=60).json()
    try:   # Türkçe şehir adları varsa onları kullan
        cities_list = requests.get(f"{base}/tr/cities.json", timeout=60).json()
        assert isinstance(cities_list, list) and cities_list
    except Exception:
        cities_list = requests.get(f"{base}/en/cities.json", timeout=60).json()
    cities = {c["code"]: c for c in cities_list}
    rows = []
    for a in airports:
        if a.get("iata_type") != "airport" or not a.get("flightable") or not a.get("coordinates"):
            continue
        city = cities.get(a.get("city_code") or "", {})
        name = (city.get("name_translations") or {}).get("tr") or city.get("name") or a.get("name")
        country = a.get("country_code")
        if a["code"] == "ECN":
            country = "XN"   # KKTC
        rows.append([a["code"], a.get("city_code") or a["code"], name, country,
                     a["coordinates"]["lat"], a["coordinates"]["lon"]])
    FULL_FILE.write_text(json.dumps({"airports": rows}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(rows)} havalimanı kaydedildi -> {FULL_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
