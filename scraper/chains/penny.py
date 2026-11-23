# -*- coding: utf-8 -*-
"""Penny – akční položky z Nuxt payloadu stránky /nabidky.

Web publikuje jen výběr akcí (ne celý leták); nabídka je celostátní.
Ceny jsou v haléřích.
"""

import json
import re

from chains.common import HEADERS, get_session, is_beer, make_item

URL = "https://www.penny.cz/nabidky"
NUXT_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _resolve(idx, arr, depth=0):
    """Rozbalí Nuxt/devalue payload (hodnoty jsou indexy do plochého pole)."""
    if depth > 10 or not isinstance(idx, int) or not (0 <= idx < len(arr)):
        return None
    v = arr[idx]
    if isinstance(v, dict):
        return {k: _resolve(i, arr, depth + 1) for k, i in v.items()}
    if isinstance(v, list):
        if v and isinstance(v[0], str) and v[0] in (
                "Reactive", "ShallowReactive", "Ref", "ShallowRef"):
            return _resolve(v[1], arr, depth + 1)
        return [_resolve(i, arr, depth + 1) for i in v]
    return v


def scrape(today):
    s = get_session()
    html = s.get(URL, headers=HEADERS, timeout=40).text
    m = NUXT_RE.search(html)
    if not m:
        raise ValueError("__NUXT_DATA__ nenalezeno")
    arr = json.loads(m.group(1))

    items, seen = [], set()
    for i, v in enumerate(arr):
        if not (isinstance(v, dict) and "price" in v and "sku" in v):
            continue
        p = _resolve(i, arr)
        if not isinstance(p, dict) or not p.get("name"):
            continue

        cats = " ".join(
            c.get("name", "")
            for group in (p.get("parentCategories") or []) if isinstance(group, list)
            for c in group if isinstance(c, dict)
        )
        if not (is_beer(p["name"]) or "pivo" in cats.lower()):
            continue

        price_info = p.get("price") or {}
        regular = (price_info.get("regular") or {}).get("value")
        if regular is None:
            continue
        price = regular / 100
        note = None
        loyalty = (price_info.get("loyalty") or {}).get("value")
        if loyalty and loyalty / 100 < price:
            price = loyalty / 100
            note = "s PENNY kartou"

        key = p.get("sku") or p["name"]
        if key in seen:
            continue
        seen.add(key)

        volume = None
        amount, unit = p.get("amount"), p.get("volumeLabelShort")
        if amount and unit:
            volume = f"{amount} {unit}".replace(".", ",")

        per_std = (price_info.get("loyalty") if note else
                   price_info.get("regular") or {}).get("perStandardizedQuantity")
        per_l = (per_std / 100
                 if per_std and price_info.get("baseUnitShort") == "l" else None)

        items.append(make_item(
            name=p["name"],
            price=price,
            chain="Penny",
            volume=volume,
            price_per_l=per_l,
            valid_from=price_info.get("validityStart"),
            valid_to=price_info.get("validityEnd"),
            note=note,
        ))
    return items
