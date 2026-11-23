# -*- coding: utf-8 -*-
"""COOP – letáky Terno České Budějovice (Jednota ČB) z terno.cz.

Letáky jsou flip-booky s JSON textovou vrstvou (span + souřadnice).
Produkty se rekonstruují prostorovým shlukováním; cena se přijme jen
tehdy, když sedí s jednotkovou cenou (1 l = …), jinak se položka zahodí.
"""

import html as html_mod
import json
import math
import re
import time
import urllib.parse

from chains.common import (BRAND_RE, DELAY_S, HEADERS, get_session, is_beer,
                           parse_price, parse_volume_l, make_item)
from utils import infer_date

STORE_URL = "https://www.terno.cz/prodejny/ceske-budejovice/"
PAGE_URL = ("https://www.terno.cz/wp-content/uploads/real3d-flipbook/"
            "flipbook_{book}/{page}.json")

PRICE_RE = re.compile(r"^\d{1,4},(?:\d\d|[–-])$")
UNIT_RE = re.compile(r"1\s*l\s*=\s*([\d\s]+,\d+)")
VALIDITY_RE = re.compile(
    r"od\s*(\d{1,2})\.\s*(\d{1,2})\.\s*do\s*(\d{1,2})\.\s*(\d{1,2})\.")
SPAN_RE = re.compile(
    r'<span[^>]*style="left:\s*([\d.]+)px;\s*top:\s*([\d.]+)px;'
    r'\s*font-size:\s*([\d.]+)px[^"]*"[^>]*>([^<]*)</span>')


def _spans(s, book, page):
    r = s.get(PAGE_URL.format(book=book, page=page), headers=HEADERS, timeout=30)
    if r.status_code != 200:
        return None
    decoded = urllib.parse.unquote(r.json().get("data", ""))
    out = []
    for m in SPAN_RE.finditer(decoded):
        t = html_mod.unescape(m.group(4)).strip()
        if t:
            out.append({"x": float(m.group(1)), "y": float(m.group(2)),
                        "fs": float(m.group(3)), "t": t})
    return out


def scrape(today):
    s = get_session()
    page_html = s.get(STORE_URL, headers=HEADERS, timeout=40).text

    books = []
    for m in re.finditer(r'data-flipbook-options="([^"]+)"', page_html):
        opts = html_mod.unescape(m.group(1))
        bid = re.search(r'"bookId":"(\d+)"', opts)
        npages = opts.count('"json"')
        if bid and npages:
            books.append((bid.group(1), npages))

    items, seen = [], set()
    for book, npages in books:
        for page in range(1, npages + 1):
            time.sleep(DELAY_S)
            spans = _spans(s, book, page)
            if spans is None:
                break

            valid_from = valid_to = None
            for sp in spans:
                vm = VALIDITY_RE.search(sp["t"])
                if vm:
                    d1 = infer_date(int(vm.group(1)), int(vm.group(2)), today)
                    d2 = infer_date(int(vm.group(3)), int(vm.group(4)), today)
                    valid_from = d1.isoformat() if d1 else None
                    valid_to = d2.isoformat() if d2 else None
                    break

            price_spans = [sp for sp in spans
                           if sp["fs"] > 15 and PRICE_RE.match(sp["t"])]
            used_prices = set()

            # nejdřív řádky se značkou (skutečné názvy), pak obecné ("světlé pivo")
            beer_spans = sorted(
                (sp for sp in spans if is_beer(sp["t"])),
                key=lambda sp: 0 if BRAND_RE.search(sp["t"]) else 1)

            for nm in beer_spans:
                # blok textu kolem názvu (stejný sloupec)
                near = [sp for sp in spans
                        if abs(sp["x"] - nm["x"]) < 140
                        and -40 < sp["y"] - nm["y"] < 90]
                block = " ".join(sp["t"] for sp in
                                 sorted(near, key=lambda sp: (sp["y"], sp["x"])))

                unit_m = UNIT_RE.search(block)
                unit_price = parse_price(unit_m.group(1)) if unit_m else None
                volume_l = parse_volume_l(block)

                # kandidáti na cenu poblíž, ověření proti jednotkové ceně
                cands = [sp for sp in price_spans
                         if id(sp) not in used_prices
                         and abs(sp["y"] - nm["y"]) < 140
                         and abs(sp["x"] - nm["x"]) < 260]
                price_span = None
                if unit_price and volume_l:
                    expected = unit_price * volume_l
                    valid = [sp for sp in cands
                             if abs(parse_price(sp["t"]) - expected)
                             <= max(1.0, 0.12 * expected)]
                    if valid:
                        price_span = min(valid, key=lambda sp: math.hypot(
                            sp["x"] - nm["x"], sp["y"] - nm["y"]))
                if price_span is None:
                    continue  # bez ověřené ceny položku raději zahodíme
                used_prices.add(id(price_span))
                price = parse_price(price_span["t"])

                key = (nm["t"].lower(), price)
                if key in seen:
                    continue
                seen.add(key)

                vol_text = None
                vm2 = re.search(r"\d+(?:,\d+)?\s*l\b|\d+\s*ml\b", block)
                if vm2:
                    vol_text = vm2.group(0)

                items.append(make_item(
                    name=nm["t"].title() if nm["t"].isupper() else nm["t"],
                    price=price,
                    chain="COOP",
                    shop="Terno České Budějovice",
                    volume=vol_text,
                    volume_l=volume_l,
                    price_per_l=unit_price,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    note="dostupné pouze: prodejny Terno (Jednota České Budějovice)",
                ))
    return items
