"""Hedef ülke grupları.

Vize bilgisi: T.C. Dışişleri Bakanlığı "Türk Vatandaşlarının Tabi Olduğu Vize
Uygulamaları" sayfası, umuma mahsus (bordo) pasaport, Kasım 2026. Kurallar sık
değişir; seyahatten önce mfa.gov.tr'den kontrol edin.
"""
from __future__ import annotations

from dataclasses import dataclass

VISA_INFO_DATE = "Kasım 2026"

SCHENGEN = {
    "AT", "BE", "BG", "CH", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IS",
    "IT", "LI", "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
}

# Bordo pasaportla vizesiz (bazılarında önceden çevrimiçi kayıt gerekir: KR K-ETA, ID, KZ, SC)
VISA_FREE = {
    "AL", "AZ", "BA", "BY", "GE", "XK", "KX", "MK", "MD", "RS", "UA", "XN",          # Avrupa / yakın
    "IR", "JO", "LB", "QA", "OM",                                               # Orta Doğu
    "KZ", "KG", "UZ", "MN",                                                     # Orta Asya
    "JP", "KR", "HK", "MO", "SG", "TH", "PH", "ID", "BN",                       # Asya
    "MA", "TN", "MU", "SC", "BW", "ZM", "AO",                                   # Afrika
    "BR", "AR", "CL", "CO", "EC", "PE", "UY", "PY", "BO", "VE", "CR", "PA",     # Amerika
    "GT", "HN", "SV", "NI", "JM", "BB", "BS", "TT", "FJ",
}

# Kapıda vize ya da e-vize (önceden konsolosluğa gitmeden)
EASY_VISA = {
    "EG", "MV", "MY", "KW", "NP", "KH", "LK", "AM", "BH", "SA", "RU", "VN", "TW",
    "KE", "TZ", "MX", "ZA", "DO", "MZ", "NA",
}

BALKANS = {"AL", "BA", "BG", "GR", "HR", "XK", "KX", "ME", "MK", "RO", "RS", "SI"}
CAUCASUS = {"GE", "AM", "AZ"}
MIDDLE_EAST = {"AE", "QA", "SA", "JO", "LB", "OM", "BH", "KW", "IL", "IR", "IQ"}
EUROPE = SCHENGEN | BALKANS | {"GB", "IE", "CY", "XN", "MD", "UA", "BY", "ME", "RS", "AL", "BA", "MK", "XK", "KX"}


@dataclass(frozen=True)
class Region:
    key: str
    label: str
    include: frozenset | None = None      # None = kısıtlama yok
    exclude: frozenset | None = None
    note: str = ""


REGIONS: dict[str, Region] = {r.key: r for r in [
    Region("her-yer", "Her yer"),
    Region("yurtdisi", "Yurt dışı", exclude=frozenset({"TR"})),
    Region("vizesiz", "Vizesiz ülkeler", include=frozenset(VISA_FREE),
           note=f"Bordo pasaport, {VISA_INFO_DATE} Dışişleri listesi. Bazı ülkeler önceden çevrimiçi kayıt istiyor."),
    Region("kolay-vize", "Vizesiz + kapıda/e-vize", include=frozenset(VISA_FREE | EASY_VISA),
           note=f"{VISA_INFO_DATE} Dışişleri listesi."),
    Region("schengen", "Schengen", include=frozenset(SCHENGEN), note="Schengen vizesi gerekir."),
    Region("avrupa", "Avrupa", include=frozenset(EUROPE)),
    Region("balkanlar", "Balkanlar", include=frozenset(BALKANS)),
    Region("kafkasya", "Kafkasya", include=frozenset(CAUCASUS)),
    Region("ortadogu", "Orta Doğu", include=frozenset(MIDDLE_EAST)),
    Region("yurtici", "Yurt içi", include=frozenset({"TR"})),
]}

ALIASES = {
    "heryer": "her-yer", "yurtdisi": "yurtdisi", "yurtdışı": "yurtdisi",
    "vizesiz": "vizesiz", "kolayvize": "kolay-vize", "kapidavize": "kolay-vize", "evize": "kolay-vize",
    "schengen": "schengen", "şengen": "schengen", "sengen": "schengen", "avrupa": "avrupa",
    "balkan": "balkanlar", "balkanlar": "balkanlar", "kafkas": "kafkasya", "kafkasya": "kafkasya",
    "ortadogu": "ortadogu", "ortadoğu": "ortadogu", "yurtici": "yurtici", "yurtiçi": "yurtici",
}


def find_region(word: str) -> Region | None:
    w = word.lower().replace("-", "").replace(" ", "")
    key = ALIASES.get(w) or (w if w in REGIONS else None)
    if key is None:
        for k in REGIONS:
            if k.replace("-", "") == w:
                key = k
    return REGIONS.get(key) if key else None
