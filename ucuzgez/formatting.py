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


def short_day(d: date) -> str:
    return f"{d.day} {TR_MONTHS[d.month - 1]}"


def trip_title(t: Trip, ap: AirportDB) -> str:
    dest = ap.city_name(t.dest_city)
    if t.open_jaw:
        return f"{dest}, dönüş {ablative(ap.city_name(t.return_city))}"
    return dest


def trip_telegram(t: Trip, ap: AirportDB, cur: str, idx: int | None = None) -> str:
    """Telegram için sade, emojisiz özet."""
    e = lambda x: html.escape(x, quote=False)   # noqa: E731
    o, i = t.outbound, t.inbound
    head = f"{idx}. " if idx is not None else ""

    def leg(label: str, f) -> str:
        links = " ".join(f'<a href="{html.escape(u)}">{e(n)}</a>' for n, u in leg_links(f)[:2])
        when = short_day(f.depart) + (f" {f.depart_time}" if f.depart_time else "")
        extra = f", {f.transfers} aktarma" if f.transfers else ""
        return (f"{label}: {when}, {f.origin}–{f.destination}, "
                f"{e(airline_name(f.airline))}{extra}, {money(f.price, cur)}\n   {links}")

    lines = [f"<b>{head}{e(trip_title(t, ap))}</b>",
             f"<b>{money(t.total, cur)}</b> toplam, {t.days} gün ({day_str(o.depart)} – {day_str(i.depart)})"
             + (", canlı fiyat" if t.verified else "")]
    lines.append(leg("Gidiş", o))
    if t.ground:
        lines.append(f"Yol: {ap.city_name(t.ground.from_city)}–{ap.city_name(t.ground.to_city)} otobüs/tren, "
                     f"yaklaşık {t.ground.hours:g} saat, ~{money(t.ground_cost, cur)} (tahmini)")
    lines.append(leg("Dönüş", i))
    if t.saving:
        lines.append(f"{ablative(ap.city_name(t.dest_city))} dönmek {money(t.saving, cur)} daha pahalı olurdu.")
    return "\n".join(lines)
