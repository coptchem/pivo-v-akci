# -*- coding: utf-8 -*-
"""Billa – veřejné product-discovery API, kategorie Pivo.

Ceny jsou v haléřích a celostátní. API bohužel nevrací platnost akce
(valid_from/valid_to zůstávají prázdné).
"""

import time

from chains.common import (DELAY_S, HEADERS, NOT_BEER_RE, get_session,
                           make_item)

URL = ("https://www.billa.cz/api/product-discovery/categories/"
       "pivo-2481/products")


def scrape(today):
    s = get_session()
    items, offset, total = [], 0, None

    while total is None or offset < total:
        r = s.get(URL, params={"pageSize": 100, "offset": offset},
                  headers={**HEADERS, "Accept": "application/json"}, timeout=40)
        r.raise_for_status()
        d = r.json()
        total = d["total"]

        for p in d["results"]:
            if not p.get("inPromotion"):
                continue
            if NOT_BEER_RE.search(p.get("name", "")):
                continue  # kategorie Pivo obsahuje i cidery
            price_info = p["price"]
            price = price_info["regular"]["value"] / 100
            crossed = price_info.get("crossed")
            note = None
            if crossed:
                note = f"původně {crossed / 100:.2f} Kč".replace(".", ",")

            volume_l = p.get("amount")
            per_std = price_info["regular"].get("perStandardizedQuantity")
            per_l = per_std / 100 if per_std else None

            items.append(make_item(
                name=p["name"],
                price=price,
                chain="Billa",
                volume=(f"{volume_l} l".replace(".", ",") if volume_l else None),
                volume_l=volume_l,
                price_per_l=per_l,
                note=note,
            ))

        offset += d["count"]
        if offset < total:
            time.sleep(DELAY_S)

    return items
