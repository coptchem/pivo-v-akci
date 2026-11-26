# -*- coding: utf-8 -*-
"""Porovnání vytěžených nabídek se zlatou sadou (ručně ověřené stránky)."""

import re
from difflib import SequenceMatcher

from chains.brands import brand_of
from utils import parse_volume_l

NAME_SIMILARITY = 0.6


def _norm_name(name):
    """Malá písmena bez objemů a interpunkce; lomítko mezi druhy zůstane
    jako dvojitá mezera (viz same_offer)."""
    t = str(name or "").lower()
    t = re.sub(r"\d+\s*[x×]\s*\d+(?:[.,]\d+)?\s*l\b", " ", t)
    t = re.sub(r"\d+(?:[.,]\d+)?\s*l\b", " ", t)
    parts = [re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", p)).strip()
             for p in t.split("/")]
    return "  ".join(p for p in parts if p)


def _volume(o):
    v = o.get("volume")
    return parse_volume_l(v) if isinstance(v, str) else None


def _contains(a, b):
    """Jeden název je začátkem nebo částí druhého po celých slovech
    („cecilia 11“ v „cecilia 11 bezlepkový ležák“)."""
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 3 and f" {short} " in f" {long_} "


def same_offer(expected, predicted):
    """Stejná nabídka = stejná akční cena a podobný název (nebo značka+objem)."""
    if not isinstance(predicted.get("price"), (int, float)):
        return False
    if abs(expected["price"] - predicted["price"]) > 0.01:
        return False
    a, b = _norm_name(expected["name"]), _norm_name(predicted["name"])
    if SequenceMatcher(None, a, b).ratio() >= NAME_SIMILARITY:
        return True
    # název obsažený v druhém, i po částech společné nabídky více druhů
    # („mustang / černá barbora“ proti „mustang“)
    if any(_contains(pa, pb) for pa in a.split("  ") for pb in b.split("  ")):
        return True
    brand = brand_of(expected["name"])
    return brand is not None and brand == brand_of(predicted["name"]) \
        and _volume(expected) == _volume(predicted)


def _shown_per_litre(o):
    """Cena za litr, jak ji ukáže web: z letáku, jinak dopočtená z objemu
    (stejně jako make_item)."""
    per_l = o.get("price_per_l")
    if isinstance(per_l, (int, float)):
        return per_l
    vol = _volume(o)
    price = o.get("price")
    return round(price / vol, 2) if vol and isinstance(price, (int, float)) \
        else None


def _close(a, b, rel=0.0, abs_=0.01):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(abs_, rel * abs(a))


def compare(expected, predicted):
    """Spáruje nabídky jedné stránky. Vrací počty pro precision/recall
    a shodu polí u spárovaných nabídek."""
    left = list(predicted)
    matched = []
    for e in expected:
        for i, p in enumerate(left):
            if same_offer(e, p):
                matched.append((e, left.pop(i)))
                break
    fields = {"volume": 0, "old_price": 0, "price_per_l": 0}
    for e, p in matched:
        fields["volume"] += _close(_volume(e), _volume(p), abs_=0.001)
        fields["old_price"] += _close(e.get("old_price"), p.get("old_price"))
        fields["price_per_l"] += _close(_shown_per_litre(e),
                                        _shown_per_litre(p), rel=0.02)
    return {
        "expected": len(expected),
        "predicted": len(predicted),
        "matched": len(matched),
        "fields": fields,
        "missed": [e["name"] for e in expected
                   if not any(e is m for m, _ in matched)],
        "extra": [f"{p.get('name')} ({p.get('price')} Kč)" for p in left],
    }


def summarize(rows):
    exp = sum(r["expected"] for r in rows)
    pred = sum(r["predicted"] for r in rows)
    hit = sum(r["matched"] for r in rows)
    fields = {k: sum(r["fields"][k] for r in rows)
              for k in ("volume", "old_price", "price_per_l")}
    return {
        "pages": len(rows),
        "precision": hit / pred if pred else 1.0,
        "recall": hit / exp if exp else 1.0,
        "field_accuracy": {k: (v / hit if hit else 1.0)
                           for k, v in fields.items()},
    }
