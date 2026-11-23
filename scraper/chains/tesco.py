# -*- coding: utf-8 -*-
"""Tesco – GraphQL API akčních nabídek (leaflets-be).

Akce jsou celostátní podle formátu prodejny; položky dostávají poznámku
"jen hypermarkety" / "jen supermarkety", pokud neplatí ve všech.
"""

import json
import re
from datetime import datetime, timezone

from chains.common import HEADERS, get_session, is_beer, make_item, \
    per_litre_from_text

ENDPOINT = "https://api.prod.retail.tesco.com/marketing/leaflets-be/graphql"
CATEGORY_DRINKS = 2769  # Nápoje (pivo nemá vlastní kategorii)

QUERY = """query promotions($country: CountryCode!, $today: DateTime!, $categoryId: Int,
  $sort: PromotionPeriodSortParameters, $take: Int, $skip: Int) {
  promotions(options: {country: $country, today: $today,
      sort: $sort, skip: $skip, take: $take, categoryId: $categoryId}) {
    items { id displayedProductName displayedDescription normalRetailPrice normalNewPrice
      ccPrice promoStart promoEnd saving ccSaving availableInHm availableInSm availableInEx }
    totalItems
  }
}"""


def _fetch(s, skip, take=100):
    variables = {
        "country": "cz",
        "today": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00.000Z"),
        "categoryId": CATEGORY_DRINKS,
        "sort": {"name": "ASC"},
        "skip": skip,
        "take": take,
    }
    r = s.get(ENDPOINT, params={
        "query": QUERY,
        "variables": json.dumps(variables),
        "operationName": "promotions",
    }, headers={
        **HEADERS,
        "Accept": "application/json",
        "x-apollo-operation-name": "promotions",
        "Origin": "https://www.itesco.cz",
        "Referer": "https://www.itesco.cz/",
    }, timeout=40)
    r.raise_for_status()
    d = r.json()
    if "errors" in d:
        raise RuntimeError(json.dumps(d["errors"])[:300])
    return d["data"]["promotions"]


def scrape(today):
    s = get_session()
    raw, skip, total = [], 0, None
    while total is None or skip < total:
        promos = _fetch(s, skip)
        total = promos["totalItems"]
        batch = promos["items"]
        if not batch:
            break
        skip += len(batch)
        raw.extend(batch)

    # stejná akce bývá v seznamu dvakrát (HM a SM varianta) - sloučit
    grouped = {}
    for it in raw:
        desc = it.get("displayedDescription") or ""
        if not is_beer(f'{it["displayedProductName"]} {desc}'):
            continue
        key = (it["displayedProductName"], it.get("normalNewPrice"),
               it.get("ccPrice"), it.get("promoStart"), it.get("promoEnd"))
        entry = grouped.setdefault(key, {"it": it, "hm": False, "sm": False})
        entry["hm"] = entry["hm"] or bool(it.get("availableInHm"))
        entry["sm"] = entry["sm"] or bool(it.get("availableInSm"))

    items = []
    for entry in grouped.values():
        it = entry["it"]
        desc = it.get("displayedDescription") or ""
        price = it.get("normalNewPrice")
        notes = []
        cc = it.get("ccPrice")
        if cc is not None and (price is None or cc < price):
            price = cc
            notes.append("s Clubcard")
        if price is None:
            continue
        old = it.get("normalRetailPrice")
        if old and old > price:
            notes.append(f"původně {old:.2f} Kč".replace(".", ","))
        if entry["hm"] and not entry["sm"]:
            notes.append("jen hypermarkety")
        elif entry["sm"] and not entry["hm"]:
            notes.append("jen supermarkety")

        vol_m = re.search(r"\d+\s*[×x]\s*\d+(?:[.,]\d+)?\s*l\b"
                          r"|\d+(?:[.,]\d+)?\s*l\b|\d+\s*ml\b", desc, re.I)
        items.append(make_item(
            name=it["displayedProductName"],
            price=price,
            chain="Tesco",
            volume=vol_m.group(0) if vol_m else None,
            price_per_l=per_litre_from_text(desc),
            valid_from=(it.get("promoStart") or "")[:10] or None,
            valid_to=(it.get("promoEnd") or "")[:10] or None,
            note="; ".join(notes) or None,
        ))
    return items
