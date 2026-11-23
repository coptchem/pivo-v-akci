# -*- coding: utf-8 -*-
"""Lidl – JSON API webu lidl.cz (gridboxes = akční produkty, + fulltext).

Nabídka je celostátní, prodejnu není třeba vybírat.
"""

import re
import time
from datetime import date

from chains.common import (DELAY_S, HEADERS, NOT_BEER_RE, get_session,
                           make_item, per_litre_from_text)

GRID_URL = "https://www.lidl.cz/p/api/gridboxes/CZ/cs?max=400"
SEARCH_URL = ("https://www.lidl.cz/q/api/search?q=pivo&offset={offset}"
              "&fetchsize=30&locale=cs_CZ&assortment=CZ&version=2.1.0")


def _epoch_to_iso(v):
    return date.fromtimestamp(v).isoformat() if v else None


def _norm(item):
    kf = item.get("keyfacts") or {}
    if "Pivo" not in (kf.get("wonCategoryPrimary") or ""):
        return None  # kategorie "…/Pivo a cider"
    name = item.get("fullTitle") or ""
    brand = ((item.get("brand") or {}).get("name") or "").strip()
    if brand and not name.lower().startswith(brand.lower()):
        name = f"{brand} {name}"
    # "ARGUS ARGUS 10" -> "ARGUS 10"
    name = re.sub(r"^(\S+)\s+\1\b", r"\1", name, flags=re.I)
    if NOT_BEER_RE.search(name):
        return None  # cidery apod.

    pr = item.get("price") or {}
    price = pr.get("price")
    old = (pr.get("discount") or {}).get("deletedPrice")
    note = None
    if price is None and item.get("lidlPlus"):
        lp = item["lidlPlus"][0].get("price") or {}
        price, old = lp.get("price"), lp.get("oldPrice")
        pr = lp
        note = "s aplikací Lidl Plus"

    valid_from = _epoch_to_iso(item.get("storeStartDate"))
    valid_to = _epoch_to_iso(item.get("storeEndDate"))
    if price is None or (old is None and not valid_from):
        return None  # běžný sortiment bez akce

    if old:
        note = (note + "; " if note else "") + f"původně {old:.2f} Kč".replace(".", ",")

    volume = ((pr.get("packaging") or {}).get("text") or "").strip()
    return make_item(
        name=name,
        price=price,
        chain="Lidl",
        volume=volume or None,
        price_per_l=per_litre_from_text((pr.get("basePrice") or {}).get("text")),
        valid_from=valid_from,
        valid_to=valid_to,
        note=note,
    )


def scrape(today):
    s = get_session()
    out, seen = [], set()

    for item in s.get(GRID_URL, headers=HEADERS, timeout=40).json():
        b = _norm(item)
        if b and b["name"] not in seen:
            seen.add(b["name"])
            out.append(b)

    offset = 0
    while True:
        time.sleep(DELAY_S)
        d = s.get(SEARCH_URL.format(offset=offset), headers=HEADERS, timeout=40).json()
        results = d.get("items", [])
        if not results:
            break
        for res in results:
            gb = (res.get("gridbox") or {}).get("data")
            if not gb:
                continue
            b = _norm(gb)
            if b and b["name"] not in seen:
                seen.add(b["name"])
                out.append(b)
        offset += len(results)
        if offset >= d.get("numFound", 0):
            break

    return out
