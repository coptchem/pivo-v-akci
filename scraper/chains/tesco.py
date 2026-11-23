# -*- coding: utf-8 -*-
"""Tesco – PDF letáky (seznam z leaflets-be GraphQL) -> Claude API.

Veřejný GraphQL dotaz na akční nabídky Tesco zrušilo; seznam letáků
(s odkazy na PDF) ale zůstal veřejný. Bere se nejnovější dvojice týdenních
period českých letáků ve verzi pro hypermarkety (HM) a supermarkety (SM),
piva z nich vytáhne Claude API (chains/leaflet_ai), výsledky se cachují
podle ID letáku. Akce, které jsou jen v jedné verzi, dostanou poznámku
"jen hypermarkety" / "jen supermarkety".
"""

import re

from chains import leaflet_ai
from chains.common import HEADERS, NOT_BEER_RE, get_session, make_item, \
    parse_volume_l

ENDPOINT = "https://api.prod.retail.tesco.com/marketing/leaflets-be/graphql"
QUERY = "{ leaflets { items { id leafletUrl } } }"
PERIODS = 2   # aktuální + následující týdenní leták

# .../20261119063349791_2026_P38_CZ_HM-CHM.pdf (katalogy mají P38-GIFT-…)
PDF_RE = re.compile(r"/\d+_(\d{4})_P(\d+)_CZ_(HM-CHM|SM)\.pdf$")


def _leaflets(s):
    r = s.post(ENDPOINT, json={"query": QUERY}, headers={
        **HEADERS,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.itesco.cz",
        "Referer": "https://www.itesco.cz/",
    }, timeout=40)
    r.raise_for_status()
    d = r.json()
    if "errors" in d:
        raise RuntimeError(str(d["errors"])[:300])

    found = []
    for lf in d["data"]["leaflets"]["items"]:
        m = PDF_RE.search(lf.get("leafletUrl") or "")
        if m:
            found.append({"id": lf["id"], "url": lf["leafletUrl"],
                          "period": (int(m.group(1)), int(m.group(2))),
                          "format": "HM" if m.group(3) != "SM" else "SM"})
    periods = sorted({lf["period"] for lf in found})[-PERIODS:]
    return [lf for lf in found if lf["period"] in periods]


def scrape(today):
    client = leaflet_ai.make_client()
    s = get_session()
    leaflets = _leaflets(s)
    if not leaflets:
        raise ValueError("žádné letáky nenalezeny")

    cache = leaflet_ai.LeafletCache("Tesco")
    cache.prune(lf["id"] for lf in leaflets)

    grouped = {}
    for lf in leaflets:
        raws = cache.get(lf["id"])
        if raws is None:
            pdf = s.get(lf["url"], headers=HEADERS, timeout=300)
            pdf.raise_for_status()
            raws = leaflet_ai.extract_pages(
                client, leaflet_ai.render_pdf(pdf.content), "Tesco",
                today.year)
            cache.put(lf["id"], raws)
            print(f"Tesco: leták P{lf['period'][1]} {lf['format']}: "
                  f"{len(raws)} piv (nové)")
        for raw in raws:
            if NOT_BEER_RE.search(raw.get("name", "")) \
                    or not leaflet_ai.is_beer_offer(raw):
                continue
            key = (raw["name"].lower(), raw["price"], raw.get("valid_from"))
            entry = grouped.setdefault(key, {"raw": raw, "formats": set()})
            entry["formats"].add(lf["format"])

    items = []
    for entry in grouped.values():
        raw = entry["raw"]
        valid_to = raw.get("valid_to")
        if valid_to and valid_to < today.isoformat():
            continue  # akce ze staršího letáku už skončila
        notes = leaflet_ai.price_note(raw)
        if entry["formats"] == {"HM"}:
            notes.append("jen hypermarkety")
        elif entry["formats"] == {"SM"}:
            notes.append("jen supermarkety")
        volume = raw.get("volume")
        per_l = raw.get("price_per_l")
        items.append(make_item(
            name=str(raw["name"]),
            price=raw["price"],
            chain="Tesco",
            volume=volume,
            volume_l=parse_volume_l(volume or ""),
            price_per_l=per_l if isinstance(per_l, (int, float)) else None,
            valid_from=raw.get("valid_from"),
            valid_to=valid_to,
            note="; ".join(notes) or None,
        ))
    return items
