"""Hedef seçimi: bölge, ülke ve şehirlerden oluşan dahil/hariç listeleri.

Belirteç (token) biçimi, web formunda ve takiplerde aynıdır:
  r:schengen   bölge        c:BG   ülke        y:BUH   şehir (IATA şehir kodu)
Örnek: dahil = [r:schengen], hariç = [c:BG, y:BUH]  ->  Bulgaristan ve Bükreş hariç Schengen.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .geo import AirportDB
from .regions import REGIONS

COUNTRY_TR = {
    "TR": "Türkiye", "XN": "KKTC", "CY": "Kıbrıs (Güney)", "GR": "Yunanistan", "BG": "Bulgaristan",
    "RO": "Romanya", "RS": "Sırbistan", "BA": "Bosna-Hersek", "ME": "Karadağ", "AL": "Arnavutluk",
    "MK": "Kuzey Makedonya", "XK": "Kosova", "KX": "Kosova", "HR": "Hırvatistan", "SI": "Slovenya",
    "HU": "Macaristan", "AT": "Avusturya", "SK": "Slovakya", "CZ": "Çekya", "PL": "Polonya",
    "DE": "Almanya", "CH": "İsviçre", "LI": "Lihtenştayn", "IT": "İtalya", "MT": "Malta", "FR": "Fransa",
    "MC": "Monako", "ES": "İspanya", "PT": "Portekiz", "AD": "Andorra", "BE": "Belçika", "NL": "Hollanda",
    "LU": "Lüksemburg", "GB": "Birleşik Krallık", "IE": "İrlanda", "DK": "Danimarka", "SE": "İsveç",
    "NO": "Norveç", "FI": "Finlandiya", "IS": "İzlanda", "EE": "Estonya", "LV": "Letonya", "LT": "Litvanya",
    "BY": "Belarus", "UA": "Ukrayna", "MD": "Moldova", "RU": "Rusya", "GE": "Gürcistan", "AM": "Ermenistan",
    "AZ": "Azerbaycan", "IR": "İran", "IQ": "Irak", "SY": "Suriye", "LB": "Lübnan", "IL": "İsrail",
    "JO": "Ürdün", "SA": "Suudi Arabistan", "AE": "Birleşik Arap Emirlikleri", "QA": "Katar", "BH": "Bahreyn",
    "KW": "Kuveyt", "OM": "Umman", "YE": "Yemen", "EG": "Mısır", "LY": "Libya", "TN": "Tunus",
    "DZ": "Cezayir", "MA": "Fas", "SD": "Sudan", "ET": "Etiyopya", "KE": "Kenya", "TZ": "Tanzanya",
    "UG": "Uganda", "RW": "Ruanda", "ZA": "Güney Afrika", "NA": "Namibya", "BW": "Botsvana", "ZM": "Zambiya",
    "ZW": "Zimbabve", "MZ": "Mozambik", "AO": "Angola", "NG": "Nijerya", "GH": "Gana", "SN": "Senegal",
    "CI": "Fildişi Sahili", "CM": "Kamerun", "MU": "Mauritius", "SC": "Seyşeller", "MG": "Madagaskar",
    "KZ": "Kazakistan", "KG": "Kırgızistan", "UZ": "Özbekistan", "TM": "Türkmenistan", "TJ": "Tacikistan",
    "AF": "Afganistan", "PK": "Pakistan", "IN": "Hindistan", "NP": "Nepal", "BD": "Bangladeş",
    "LK": "Sri Lanka", "MV": "Maldivler", "CN": "Çin", "HK": "Hong Kong", "MO": "Makao", "TW": "Tayvan",
    "MN": "Moğolistan", "JP": "Japonya", "KR": "Güney Kore", "TH": "Tayland", "VN": "Vietnam",
    "KH": "Kamboçya", "LA": "Laos", "MM": "Myanmar", "MY": "Malezya", "SG": "Singapur", "ID": "Endonezya",
    "PH": "Filipinler", "BN": "Brunei", "AU": "Avustralya", "NZ": "Yeni Zelanda", "FJ": "Fiji",
    "US": "ABD", "CA": "Kanada", "MX": "Meksika", "CU": "Küba", "DO": "Dominik Cumhuriyeti", "JM": "Jamaika",
    "BS": "Bahamalar", "BB": "Barbados", "TT": "Trinidad ve Tobago", "GT": "Guatemala", "HN": "Honduras",
    "SV": "El Salvador", "NI": "Nikaragua", "CR": "Kosta Rika", "PA": "Panama", "CO": "Kolombiya",
    "VE": "Venezuela", "EC": "Ekvador", "PE": "Peru", "BO": "Bolivya", "BR": "Brezilya", "PY": "Paraguay",
    "UY": "Uruguay", "AR": "Arjantin", "CL": "Şili",
}


def fold(s: str) -> str:
    """Aramada Türkçe karakter ve büyük/küçük harf farkını yok say."""
    s = s.replace("ı", "i").replace("İ", "i").lower()
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


@dataclass
class Selection:
    """Çözümlenmiş hedef seçimi. include_* None ise kısıt yok (her yer)."""
    include_countries: set[str] | None
    include_cities: set[str] | None
    exclude_countries: set[str]
    exclude_cities: set[str]

    @property
    def only_cities(self) -> bool:
        return self.include_cities is not None and self.include_countries is None


class Places:
    def __init__(self, airports: AirportDB):
        self.ap = airports
        self._countries = sorted({a.country for a in airports.by_code.values() if a.country})

    def country_name(self, code: str) -> str:
        return COUNTRY_TR.get(code, code)

    # ---- belirteç etiketleri -----------------------------------------------------
    def label(self, token: str) -> str:
        kind, _, val = token.partition(":")
        if kind == "r" and val in REGIONS:
            return REGIONS[val].label
        if kind == "c":
            return self.country_name(val)
        if kind == "y":
            name = self.ap.city_name(val)
            c = self.ap.country_of(val)
            return f"{name}, {self.country_name(c)}" if c and c != "TR" else name
        return token

    def kind_label(self, token: str) -> str:
        return {"r": "bölge", "c": "ülke", "y": "şehir"}.get(token[:1], "")

    def valid(self, token: str) -> bool:
        kind, _, val = token.partition(":")
        if kind == "r":
            return val in REGIONS
        if kind == "c":
            return val in COUNTRY_TR or val in self._countries
        if kind == "y":
            return self.ap.city_of(val) in self.ap.by_city
        return False

    def describe(self, include: list[str] | None, exclude: list[str] | None) -> str:
        """Örn. "Schengen, Bulgaristan ve Bükreş hariç"."""
        inc = [self.label(t).split(",")[0] for t in (include or []) if t != "r:her-yer"]
        exc = [self.label(t).split(",")[0] for t in (exclude or [])]
        text = ", ".join(inc) if inc else "her yer"
        if exc:
            text += ", " + (" ve ".join([", ".join(exc[:-1]), exc[-1]]) if len(exc) > 1 else exc[0]) + " hariç"
        return text

    # ---- arama (öneri listesi) -----------------------------------------------------
    def search(self, q: str, limit: int = 8) -> list[dict]:
        fq = fold(q.strip())
        if not fq:
            return []
        out: list[tuple[int, dict]] = []

        def score(name: str, code: str = "") -> int | None:
            fn = fold(name)
            if code and fold(code) == fq:
                return 0
            if fn.startswith(fq):
                return 1
            if f" {fq}" in f" {fn}":
                return 2
            if len(fq) >= 3 and fq in fn:
                return 3
            return None

        for r in REGIONS.values():
            s = score(r.label, r.key)
            if s is not None:
                out.append((s, {"id": f"r:{r.key}", "label": r.label, "kind": "bölge"}))
        seen_c = set()
        for code in self._countries + [c for c in COUNTRY_TR if c not in self._countries]:
            if code in seen_c or code == "KX":
                continue
            seen_c.add(code)
            s = score(self.country_name(code), code)
            if s is not None:
                out.append((s + 0.1, {"id": f"c:{code}", "label": self.country_name(code), "kind": "ülke"}))
        for city, lst in self.ap.by_city.items():
            s = score(lst[0].city, city)
            if s is None:
                s2 = next((score(a.city, a.code) for a in lst if fold(a.code) == fq), None)
                s = s2
            if s is not None:
                pop = 0 if city in self.ap.popular else 0.5
                out.append((s + 0.2 + pop, {"id": f"y:{city}", "label": lst[0].city, "kind": "şehir",
                                            "sub": f"{city}, {self.country_name(lst[0].country)}"}))
        out.sort(key=lambda x: (x[0], x[1]["label"]))
        return [d for _, d in out[:limit]]

    # ---- çözümleme ----------------------------------------------------------------------
    def resolve(self, include: list[str], exclude: list[str]) -> Selection:
        inc_c: set[str] | None = None
        inc_y: set[str] | None = None
        exc_c: set[str] = set()
        exc_y: set[str] = set()
        for t in include:
            kind, _, val = t.partition(":")
            if kind == "r":
                reg = REGIONS.get(val)
                if not reg:
                    continue
                if reg.include is not None:
                    inc_c = (inc_c or set()) | set(reg.include)
                elif reg.exclude:      # "yurt dışı" gibi: her yer eksi bir şey
                    exc_c |= set(reg.exclude)
                    inc_c = inc_c  # kısıt eklemez
                else:                  # her yer
                    pass
            elif kind == "c":
                inc_c = (inc_c or set()) | ({val, "XK"} if val in ("XK", "KX") else {val})
                if val in ("XK", "KX"):
                    inc_c.add("KX")
            elif kind == "y":
                inc_y = (inc_y or set()) | {self.ap.city_of(val)}
        for t in exclude:
            kind, _, val = t.partition(":")
            if kind == "r" and val in REGIONS and REGIONS[val].include is not None:
                exc_c |= set(REGIONS[val].include)
            elif kind == "c":
                exc_c |= {val, "XK", "KX"} if val in ("XK", "KX") else {val}
            elif kind == "y":
                exc_y.add(self.ap.city_of(val))
        # "her yer" + şehir seçildiyse: şehir seçimi kısıt olur, ülke kısıtı yok
        return Selection(inc_c, inc_y, exc_c, exc_y)
