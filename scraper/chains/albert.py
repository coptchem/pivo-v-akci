# -*- coding: utf-8 -*-
"""Albert – extrakce piva z obrázkových letáků přes Claude API (Haiku 5.5).

Albert strukturovaná data nepublikuje; letáky jsou PDF na Publitas.
Postup: seznam letáků z albert.cz (__NEXT_DATA__) -> stažení PDF ->
render stránek -> Claude vytáhne akční piva jako JSON (chains/leaflet_ai).
Výsledky se cachují podle ID letáku. Bez ANTHROPIC_API_KEY modul selže
a scrape.py použije záložní zdroj.
"""

import json
import re

from chains import leaflet_ai
from chains.common import HEADERS, get_session, make_item, parse_volume_l

LEAFLETS_URL = "https://www.albert.cz/aktualni-letaky"


def _get_leaflets(s):
    r = s.get(LEAFLETS_URL, headers=HEADERS, timeout=40)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">'
                  r'(.*?)</script>', r.text, re.S)
    data = json.loads(m.group(1))

    leaflets = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("__typename") == "Leaflet" and o.get("downloadUrl"):
                leaflets.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    # unikátní podle id
    return list({lf["id"]: lf for lf in leaflets}.values())


def _cz_date_to_iso(text):
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", (text or "").strip())
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" \
        if m else None


def scrape(today):
    client = leaflet_ai.make_client()
    s = get_session()
    leaflets = _get_leaflets(s)
    if not leaflets:
        raise ValueError("žádné letáky nenalezeny")

    cache = leaflet_ai.LeafletCache("Albert")
    cache.prune(lf["id"] for lf in leaflets)

    # (name, price) -> {raw, leaflet info, locationTypes}
    grouped = {}
    for lf in leaflets:
        raws = cache.get(lf["id"])
        if raws is None:
            pdf = s.get(lf["downloadUrl"], headers=HEADERS, timeout=180)
            pdf.raise_for_status()
            raws = leaflet_ai.extract_pages(
                client, leaflet_ai.render_pdf(pdf.content), "Albert",
                today.year)
            cache.put(lf["id"], raws)
            print(f"Albert: leták {lf['title']}: {len(raws)} piv (nové)")
        for raw in raws:
            key = (str(raw.get("name", "")).lower(), raw.get("price"))
            entry = grouped.setdefault(key, {"raw": raw, "lf": lf,
                                             "types": set()})
            entry["types"].add(lf.get("locationType"))

    items = []
    for entry in grouped.values():
        raw, lf = entry["raw"], entry["lf"]
        notes = leaflet_ai.price_note(raw)
        if entry["types"] == {"HYPERMARKET"}:
            notes.append("jen hypermarkety")
        elif entry["types"] == {"SUPERMARKET"}:
            notes.append("jen supermarkety")

        volume = raw.get("volume")
        per_l = raw.get("price_per_l")
        items.append(make_item(
            name=str(raw.get("name", "")),
            price=raw["price"],
            chain="Albert",
            volume=volume,
            volume_l=parse_volume_l(volume or ""),
            price_per_l=per_l if isinstance(per_l, (int, float)) else None,
            valid_from=raw.get("valid_from")
            or _cz_date_to_iso(lf.get("validityStartDateFormatted")),
            valid_to=raw.get("valid_to")
            or _cz_date_to_iso(lf.get("validityEndDateFormatted")),
            note="; ".join(notes) or None,
        ))
    return items
