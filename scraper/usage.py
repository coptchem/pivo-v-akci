# -*- coding: utf-8 -*-
"""Spotřeba Claude API po dnech: data/usage/<datum>.json.

scrape.py sem po každém běhu zapíše volání z LangGraph pipeline letáků
(řetězec, model, počet volání a stránek, tokeny). Soubor se commitne
s ostatními daty a čte ho týdenní souhrn nákladů (cost_report.py).
"""

import json

# USD za milion tokenů (vstup, výstup), ceník Claude API, listopad 2026.
# Haiku 5.5 má tuto cenu pro prompty do 100K tokenů; stránka letáku má
# řádově jednotky tisíc, vyšší pásmo se tu nepoužije.
PRICES_USD_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


def _key(c):
    return c.get("chain") or "?", c["model"]


def record(data_dir, today, calls):
    """Přičte volání k dennímu souboru (víc běhů za den se sečte)."""
    path = data_dir / "usage" / f"{today.isoformat()}.json"
    rows = {}
    if path.exists():
        for r in json.loads(path.read_text(encoding="utf-8"))["rows"]:
            rows[(r["chain"], r["model"])] = r
    for c in calls:
        chain, model = _key(c)
        r = rows.setdefault((chain, model), {
            "chain": chain, "model": model, "calls": 0, "pages": 0,
            "input_tokens": 0, "output_tokens": 0})
        r["calls"] += 1
        r["pages"] += c.get("attempt") == 1
        r["input_tokens"] += c.get("input_tokens", 0)
        r["output_tokens"] += c.get("output_tokens", 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"date": today.isoformat(),
         "rows": sorted(rows.values(), key=lambda r: (r["chain"], r["model"]))},
        ensure_ascii=False, indent=1), encoding="utf-8")


def cost_usd(row):
    """Cena řádku v USD; None, když model není v ceníku."""
    price = PRICES_USD_PER_MTOK.get(row["model"])
    if price is None:
        return None
    return (row["input_tokens"] * price[0]
            + row["output_tokens"] * price[1]) / 1_000_000
