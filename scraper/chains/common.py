# -*- coding: utf-8 -*-
"""Společné pomůcky pro scrapery jednotlivých řetězců."""

import os
import re

from curl_cffi.requests import Session

from utils import clean, parse_price, parse_volume_l  # noqa: F401 (re-export)

PROXY_URL = os.environ.get("PROXY_URL") or None
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9",
}

DELAY_S = 1.2

# lokalita, na kterou se vážou prodejnové nabídky (Kaufland, Tesco, Globus, COOP)
LOCALITY = "České Budějovice"

# značky piv (bez obecných slov) – používá se i pro rozlišení názvu od popisu
BRAND_RE = re.compile(
    r"gambrinus|pilsner|kozel|radegast|budvar|budweiser|staropramen"
    r"|braník|branik|krušovice|krusovice|zlatopramen|březňák|breznak"
    r"|starobrno|bernard|svijan|primátor|primator|lobkowicz|černá hora"
    r"|litovel|\bzubr\b|holba|poutník|poutnik|bakalář|bakalar|rohozec"
    r"|ferdinand|\bsamson\b|\bregent\b|\bplatan\b|dudák|dudak|klášter|klaster"
    r"|rychtář|rychtar|heineken|zlatý bažant|zlaty bazant|desperados"
    r"|corona extra|stella artois|hoegaarden|leffe|paulaner|erdinger"
    r"|krombacher|warsteiner|argus|perlenbacher|\bexcelent\b"
    r"|\bprimus\b|pardál|pardal|guinness|birell",
    re.I,
)
BEER_RE = re.compile(
    r"\b(pivo|ležák|lezak|výčepní|vycepni|birell|radler)\b|" + BRAND_RE.pattern,
    re.I,
)
NOT_BEER_RE = re.compile(
    r"cider|kingswood|pivní sýr|pivn[ií] klobás|sirup|sklenic|půllitr|pullitr"
    r"|\bsýr\b|\bsyr\b|tavený|salám|pomazánk|utěrk|kapesník",
    re.I,
)


def is_beer(text):
    return bool(text) and bool(BEER_RE.search(text)) and not NOT_BEER_RE.search(text)


def get_session():
    return Session(impersonate="chrome", proxies=PROXIES)


def make_item(name, price, chain, shop=None, volume=None, volume_l=None,
              price_per_l=None, valid_from=None, valid_to=None,
              validity_text=None, note=None):
    """Sestaví položku ve společném schématu dat (data/pivo.json)."""
    if volume_l is None and volume:
        volume_l = parse_volume_l(volume)
    if price_per_l is None and volume_l:
        price_per_l = round(price / volume_l, 2)
    if validity_text is None and valid_from and valid_to:
        validity_text = f"{_fmt(valid_from)} – {_fmt(valid_to)}"
    return {
        "name": clean(name),
        "volume": volume or None,
        "volume_l": volume_l,
        "shop": shop or chain,
        "chain": chain,
        "price": round(float(price), 2),
        "price_per_l": price_per_l,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "validity_text": validity_text,
        "note": note or None,
    }


def _fmt(iso):
    y, m, d = iso.split("-")
    return f"{int(d)}. {int(m)}."


def per_litre_from_text(text):
    """'1 l = 31,80 Kč' / '(=1 l 29,80)' -> 31.8"""
    if not text:
        return None
    m = re.search(r"1\s*l\s*=?\s*([\d\s]+[.,]\d+)", text)
    return parse_price(m.group(1)) if m else None
