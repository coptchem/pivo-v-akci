# -*- coding: utf-8 -*-
"""Lidl – akční letáky (Schwarz leaflets API) + JSON API webu lidl.cz.

Potravinové akce (včetně piva) Lidl zveřejňuje jen v letácích. API letáků
vrací ke každé stránce OCR klíčová slova, takže se do Claude API posílají
jen stránky, kde se pivo vyskytuje (chains/leaflet_ai). Online katalog
(gridboxes + fulltext) se prochází navíc – občas obsahuje akční pivo
i s cenou. Nabídka je celostátní.
"""

import html
import re
import time
from datetime import date

from chains import leaflet_ai
from chains.common import (BEER_RE, DELAY_S, HEADERS, NOT_BEER_RE,
                           get_session, make_item, parse_volume_l,
                           per_litre_from_text)

LEAFLETS_PAGE = "https://www.lidl.cz/c/akcni-letak/s10008644"
FLYER_API = ("https://endpoints.leaflets.schwarz/v4/flyer"
             "?flyer_identifier={slug}&region_id=0")

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


def _online(s):
    """Akční piva z online katalogu (často prázdné – piva bývají jen v letáku)."""
    out = []
    for item in s.get(GRID_URL, headers=HEADERS, timeout=40).json():
        b = _norm(item)
        if b:
            out.append(b)

    offset = 0
    while True:
        time.sleep(DELAY_S)
        d = s.get(SEARCH_URL.format(offset=offset), headers=HEADERS,
                  timeout=40).json()
        results = d.get("items", [])
        if not results:
            break
        for res in results:
            gb = (res.get("gridbox") or {}).get("data")
            b = _norm(gb) if gb else None
            if b:
                out.append(b)
        offset += len(results)
        if offset >= d.get("numFound", 0):
            break
    return out


def _flyers(s, today):
    r = s.get(LEAFLETS_PAGE, headers=HEADERS, timeout=40)
    r.raise_for_status()
    slugs = sorted(set(re.findall(r"/l/cs/letak/([^/\"?#]+)/",
                                  html.unescape(r.text))))
    flyers = []
    for slug in slugs:
        time.sleep(DELAY_S / 2)
        f = s.get(FLYER_API.format(slug=slug), headers=HEADERS,
                  timeout=40).json().get("flyer") or {}
        end = f.get("offerEndDate") or f.get("endDate") or ""
        if f.get("pages") and end[:10] >= today.isoformat():
            flyers.append(f)
    return flyers


def _beer_pages(flyer):
    """Stránky letáku, jejichž OCR text zmiňuje pivo."""
    out = []
    for p in flyer["pages"]:
        text = f"{p.get('keyWords') or ''} {p.get('altText') or ''}"
        if BEER_RE.search(text) and p.get("zoom"):
            out.append(p["zoom"])
    return out


def _from_leaflets(s, today):
    client = leaflet_ai.make_client()
    flyers = _flyers(s, today)
    if not flyers:
        raise ValueError("žádné aktuální letáky")
    cache = leaflet_ai.LeafletCache("Lidl")
    cache.prune(f["id"] for f in flyers)

    items = []
    for f in flyers:
        raws = cache.get(f["id"])
        if raws is None:
            def fetch(url):
                return lambda: leaflet_ai.shrink_jpeg(
                    s.get(url, headers=HEADERS, timeout=60).content)
            pages = [fetch(u) for u in _beer_pages(f)]
            raws = leaflet_ai.extract_pages(client, pages, "Lidl", today.year)
            cache.put(f["id"], raws)
            print(f"Lidl: leták {f.get('name') or f['id']}: "
                  f"{len(pages)} str. s pivem, {len(raws)} piv (nové)")
        for raw in raws:
            if NOT_BEER_RE.search(raw.get("name", "")) \
                    or not leaflet_ai.is_beer_offer(raw):
                continue
            if raw.get("valid_to") and raw["valid_to"] < today.isoformat():
                continue  # např. pondělní akce, která už skončila
            volume = raw.get("volume")
            per_l = raw.get("price_per_l")
            notes = leaflet_ai.price_note(raw)
            items.append(make_item(
                name=str(raw["name"]),
                price=raw["price"],
                chain="Lidl",
                volume=volume,
                volume_l=parse_volume_l(volume or ""),
                price_per_l=per_l if isinstance(per_l, (int, float)) else None,
                valid_from=raw.get("valid_from")
                or (f.get("offerStartDate") or "")[:10] or None,
                valid_to=raw.get("valid_to")
                or (f.get("offerEndDate") or "")[:10] or None,
                note="; ".join(notes) or None,
            ))
    return items


def scrape(today):
    s = get_session()
    out = _from_leaflets(s, today)
    try:
        out += _online(s)
    except Exception as e:  # online katalog je jen doplněk
        print(f"Lidl: online katalog nedostupný ({e})")

    # stejné pivo může být v letáku i v katalogu / ve dvou letácích
    seen, items = set(), []
    for it in out:
        key = (it["name"].lower(), it["price"], it.get("valid_from"))
        if key not in seen:
            seen.add(key)
            items.append(it)
    return items
