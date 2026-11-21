"""Sonuçları insan okunur hale getirme (Telegram + web ortak)."""
from __future__ import annotations

import html
from datetime import date
from urllib.parse import quote

from .geo import AirportDB
from .planner import Trip

AIRLINE_NAMES = {
    "PC": "Pegasus", "VF": "AJet", "TK": "THY", "W6": "Wizz Air", "W4": "Wizz Air Malta",
    "FR": "Ryanair", "U2": "easyJet", "XQ": "SunExpress", "LH": "Lufthansa", "OS": "Austrian",
    "EW": "Eurowings", "VY": "Vueling", "KL": "KLM", "AF": "Air France", "A3": "Aegean",
    "LO": "LOT", "OK": "Czech Airlines", "JU": "Air Serbia", "FZ": "flydubai", "J2": "AZAL",
}
AIRLINE_SITES = {"PC": "https://www.flypgs.com", "VF": "https://www.ajet.com", "TK": "https://www.turkishairlines.com"}
CUR_SYMBOL = {"try": "₺", "eur": "€", "usd": "$", "gbp": "£"}
TR_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
TR_DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def ablative(name: str) -> str:
    """Türkçe ayrılma eki: Viyana'dan, Budapeşte'den, Prag'dan, Paris'ten."""
    low = name.lower()
    vowels = [c for c in low if c in "aıoueiöü"]
    back = (vowels[-1] in "aıou") if vowels else True
    hard = low[-1] in "fstkçşhp"
    return f"{name}'{'t' if hard else 'd'}{'a' if back else 'e'}n"


def money(v: float, cur: str) -> str:
    s = f"{v:,.0f}".replace(",", ".")
    sym = CUR_SYMBOL.get(cur.lower(), cur.upper())
    return f"{s} {sym}"


def day_str(d: date) -> str:
    return f"{d.day} {TR_MONTHS[d.month - 1]} {TR_DAYS[d.weekday()]}"


def airline_name(code: str | None) -> str:
    if not code:
        return "?"
    return AIRLINE_NAMES.get(code, code)


def google_flights_link(o: str, d: str, day: date) -> str:
    return "https://www.google.com/travel/flights?q=" + quote(f"Flights from {o} to {d} on {day.isoformat()} one way")


def leg_links(f) -> list[tuple[str, str]]:
    links = []
    if f.link:
        links.append(("Aviasales" if "aviasales" in f.link else "Bilet", f.link))
    links.append(("Google Flights", google_flights_link(f.origin, f.destination, f.depart)))
    if f.airline in AIRLINE_SITES:
        links.append((airline_name(f.airline), AIRLINE_SITES[f.airline]))
    return links


def trip_telegram(t: Trip, ap: AirportDB, cur: str, idx: int | None = None) -> str:
    e = html.escape
    o, i = t.outbound, t.inbound
    head = f"{idx}. " if idx is not None else ""
    route = f"{ap.city_name(o.origin_city)} → {ap.city_name(o.destination_city)}"
    if t.open_jaw:
        route += f" ⇢ {ap.city_name(i.origin_city)} → {ap.city_name(i.destination_city)}"
    else:
        route += f" → {ap.city_name(i.destination_city)}"
    lines = [f"<b>{head}{e(route)}</b> — <b>{money(t.total, cur)}</b> ({t.days} gün)"
             + (" ✅canlı" if t.verified else "")]
    lines.append(f"🛫 {day_str(o.depart)} {o.depart_time or ''} {o.origin}→{o.destination} "
                 f"{e(airline_name(o.airline))} {money(o.price, cur)}"
                 + (f" ({o.transfers} aktarma)" if o.transfers else ""))
    if t.ground:
        lines.append(f"🚌 {ap.city_name(t.ground.from_city)}→{ap.city_name(t.ground.to_city)} "
                     f"~{t.ground.hours:g} sa, ~{money(t.ground_cost, cur)} (tahmini)")
    lines.append(f"🛬 {day_str(i.depart)} {i.depart_time or ''} {i.origin}→{i.destination} "
                 f"{e(airline_name(i.airline))} {money(i.price, cur)}"
                 + (f" ({i.transfers} aktarma)" if i.transfers else ""))
    if t.saving:
        lines.append(f"💡 {ablative(ap.city_name(t.dest_city))} dönseydin {money(t.classic_total, cur)} → "
                     f"<b>{money(t.saving, cur)} tasarruf</b>")
    links = " | ".join(f'<a href="{e(u)}">{e(n)}</a>' for n, u in leg_links(o)[:2])
    links2 = " | ".join(f'<a href="{e(u)}">{e(n)}</a>' for n, u in leg_links(i)[:2])
    lines.append(f"Gidiş: {links}\nDönüş: {links2}")
    return "\n".join(lines)
