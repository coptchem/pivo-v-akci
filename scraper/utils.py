# -*- coding: utf-8 -*-
"""Obecné pomůcky pro parsování cen, objemů a dat z letáků."""

import re
from datetime import date, timedelta

# podřetězec v názvu obchodu -> kanonický název řetězce (pro filtr v UI)
CHAIN_PATTERNS = [
    ("kaufland", "Kaufland"),
    ("penny", "Penny"),
    ("lidl", "Lidl"),
    ("albert", "Albert"),
    ("billa", "Billa"),
    ("tesco", "Tesco"),
    ("globus", "Globus"),
    ("coop", "COOP"),
]


def chain_of(shop_name):
    low = shop_name.lower()
    for pattern, canonical in CHAIN_PATTERNS:
        if pattern in low:
            return canonical
    return shop_name


def clean(text):
    return re.sub(r"\s+", " ", text).strip() if text else ""


def parse_price(text):
    """'12,90 Kč' -> 12.9"""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", text.replace("\xa0", " "))
    return float(m.group(1).replace(",", ".")) if m else None


def parse_volume_l(text):
    """'/ 0.5 l', '0,5 l', '6×0,5 l' -> objem v litrech"""
    if not text:
        return None
    t = text.replace("\xa0", " ").replace(",", ".").lower()
    m = re.search(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*l", t)
    if m:
        return round(int(m.group(1)) * float(m.group(2)), 3)
    m = re.search(r"(\d+(?:\.\d+)?)\s*l", t)
    return float(m.group(1)) if m else None


def infer_date(day, month, today):
    """Doplní rok k datu ve formátu '22. 7.' (řeší přelom roku)."""
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    if (today - d).days > 120:
        d = date(today.year + 1, month, day)
    elif (d - today).days > 240:
        d = date(today.year - 1, month, day)
    return d


def parse_validity(text, today):
    """'st 22. 7. – út 4. 8.' -> (valid_from, valid_to) jako ISO řetězce."""
    if not text:
        return None, None
    found = re.findall(r"(\d{1,2})\.\s*(\d{1,2})\.", text)
    dates = []
    for day_s, month_s in found:
        d = infer_date(int(day_s), int(month_s), today)
        if d:
            dates.append(d.isoformat())
    if len(dates) >= 2:
        return dates[0], dates[-1]
    if len(dates) == 1:
        # "do 27. 7." = jen konec, "od 24. 7." = jen začátek
        if re.search(r"\bdo\b", text):
            return None, dates[0]
        return dates[0], None
    if "dnes" in text.lower():
        return None, today.isoformat()
    if "zítra" in text.lower():
        return None, (today + timedelta(days=1)).isoformat()
    return None, None
