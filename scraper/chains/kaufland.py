# -*- coding: utf-8 -*-
"""Kaufland – akční nabídka z prodejny.kaufland.cz (SSR JSON v HTML).

Národní přehled se filtruje proti výchozímu (celostátnímu) seznamu akcí;
pár regionálních položek mimo něj se vynechává (nelze u nich určit,
kde platí).
"""

import json
import re
import time

from chains.common import (DELAY_S, HEADERS, get_session, is_beer, make_item,
                           parse_price, per_litre_from_text)

OVERVIEW_URL = "https://prodejny.kaufland.cz/nabidka/prehled.html"
DEFAULT_OFFERS_URL = "https://prodejny.kaufland.cz/.kloffers.json"


def _extract_ssr(html):
    """Najde window.SSR['...'] = {...} blok s offerData (párování závorek)."""
    for m in re.finditer(r"window\.SSR\[[^\]]+\]\s*=\s*", html):
        start = m.end()
        if html[start] != "{":
            continue
        depth = 0
        for i in range(start, len(html)):
            c = html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    blob = html[start:i + 1]
                    if "offerData" in blob:
                        return json.loads(blob)
                    break
    raise ValueError("SSR blok s offerData nenalezen")


def scrape(today):
    s = get_session()
    html = s.get(OVERVIEW_URL, headers=HEADERS, timeout=40).text
    ssr = _extract_ssr(html)

    time.sleep(DELAY_S)
    national = {x["klNr"] for x in
                s.get(DEFAULT_OFFERS_URL, headers=HEADERS, timeout=40).json()}

    items = []
    for cycle in ssr["props"]["offerData"]["cycles"]:
        for cat in cycle.get("categories", []):
            for o in cat.get("offers", []):
                text = " ".join(filter(None, [o.get("title"), o.get("subtitle"),
                                              o.get("detailTitle")]))
                if not is_beer(text):
                    continue
                price = o.get("price")
                if price is None:
                    continue

                if o.get("klNr") not in national:
                    continue  # regionální položka, neznámé prodejny

                notes = []
                old = parse_price(o.get("formattedOldPrice") or "")
                if old:
                    notes.append(f"původně {o['formattedOldPrice']}")

                name = o.get("title") or ""
                sub = (o.get("subtitle") or "").replace("\n", " ").strip()
                if sub and sub.lower() not in name.lower():
                    name = f"{name} – {sub}"

                items.append(make_item(
                    name=name,
                    price=price,
                    chain="Kaufland",
                    volume=o.get("unit"),
                    price_per_l=per_litre_from_text(o.get("formattedBasePrice")),
                    valid_from=(o.get("dateFrom") or "")[:10] or None,
                    valid_to=(o.get("dateTo") or "")[:10] or None,
                    note="; ".join(notes) or None,
                ))
    return items
