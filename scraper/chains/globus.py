# -*- coding: utf-8 -*-
"""Globus – interní API akční nabídky, všech 16 hypermarketů.

Akce se u Globusu liší podle prodejny; položka dostane poznámku
"dostupné pouze: …", pokud neplatí (skoro) všude.
"""

import re
import time

from chains.common import (HEADERS, get_session, is_beer, make_item,
                           parse_volume_l)

URL = ("https://www.globus.cz/api/v1/gsoa/actionOffers/houses/{house}"
       "/actionProducts")
PAGE_SIZE = 200  # maximum povolené API
DELAY_S = 0.8

HOUSES = {
    4001: "Brno",
    4002: "Praha-Černý Most",
    4003: "Praha-Zličín",
    4004: "Pardubice",
    4005: "Praha-Čakovice",
    4006: "Liberec",
    4007: "Ostrava",
    4008: "Olomouc",
    4009: "České Budějovice",
    4010: "Chomutov",
    4011: "Plzeň-Chotíkov",
    4012: "Opava",
    4014: "Karlovy Vary",
    4015: "Ústí n. L. (Trmice)",
    4019: "Havířov",
    4026: "Praha-Štěrboholy",
}


def _fetch_house(s, house):
    products, page = [], 0
    while True:
        r = s.get(URL.format(house=house),
                  params={"page": page, "pageSize": PAGE_SIZE},
                  headers={**HEADERS, "Accept": "application/json"}, timeout=60)
        r.raise_for_status()
        batch = r.json().get("actionProducts") or []
        if not batch:
            return products
        products.extend(batch)
        page += 1
        time.sleep(DELAY_S)


def _availability_note(cities):
    missing = [c for c in HOUSES.values() if c not in cities]
    if len(missing) <= 2:
        return f"neplatí v: {', '.join(missing)}" if missing else None
    if len(cities) <= 5:
        return "dostupné pouze: " + ", ".join(sorted(cities))
    return "jen některé prodejny Globus"


def scrape(today):
    s = get_session()

    # (klíč produktu+ceny) -> {product, cities}
    grouped = {}
    for house, city in HOUSES.items():
        try:
            products = _fetch_house(s, house)
        except Exception:
            continue  # výpadek jedné prodejny nesmí shodit celý Globus
        for p in products:
            text = f'{p.get("name", "")} {p.get("description", "")}'.replace("\n", " ")
            if not is_beer(text):
                continue
            if p.get("actualPrice") is None:
                continue
            key = (p.get("ean") or p.get("name"), p["actualPrice"])
            entry = grouped.setdefault(key, {"p": p, "cities": set()})
            entry["cities"].add(city)

    items = []
    for entry in grouped.values():
        p = entry["p"]
        price = p["actualPrice"]

        notes = []
        bonus = p.get("bonusProgramPrice")
        if bonus and 0 < bonus < price:
            price = bonus
            notes.append("s kartou Globus Bonus")
        old = p.get("originalPrice")
        if old and old > price:
            notes.append(f"původně {old:.2f} Kč".replace(".", ","))
        avail = _availability_note(entry["cities"])
        if avail:
            notes.append(avail)

        text = f'{p.get("name", "")} {p.get("description", "")}'.replace("\n", " ")
        vol_match = re.search(r"\d+\s*[x×]\s*\d+(?:[.,]\d+)?\s*l\b"
                              r"|\d+(?:[.,]\d+)?\s*l\b|\d+\s*ml\b", text, re.I)
        items.append(make_item(
            name=" ".join(filter(None, [p.get("name"),
                                        (p.get("description") or "")
                                        .replace("\n", ", ")])),
            price=price,
            chain="Globus",
            volume=vol_match.group(0) if vol_match else None,
            volume_l=parse_volume_l(vol_match.group(0)) if vol_match else None,
            valid_from=(p.get("validFrom") or "")[:10] or None,
            valid_to=(p.get("validTo") or "")[:10] or None,
            note="; ".join(notes) or None,
        ))
    return items
