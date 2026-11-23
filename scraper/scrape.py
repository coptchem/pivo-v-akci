#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stáhne akce piva z webů řetězců a uloží je do data/pivo.json.

- přímá data: Kaufland, Lidl, Billa, Tesco, Globus, Albert (letáky přes Claude API)
- záložní zdroj (scraper/fallback.py): Penny a COOP, a dále každý řetězec,
  jehož přímý scraper selže nebo vrátí prázdný výsledek

Kromě aktuálních dat ukládá denní snapshot do data/history/ (porovnání
pro e-mailová upozornění a pozdější analýzu).
Prodejnové akce nesou poznámku o dostupnosti ("dostupné pouze: …",
"jen hypermarkety" apod.).
"""

import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fallback  # noqa: E402
from chains import SCRAPERS  # noqa: E402
from chains.brands import brand_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

FALLBACK_ONLY_CHAINS = ["Penny", "COOP"]  # bez použitelného přímého zdroje


def main():
    today = date.today()

    backup_by_chain = {}
    for it in fallback.load_items(today):
        backup_by_chain.setdefault(it["chain"], []).append(it)

    all_items, status = [], {}

    for chain, fn in SCRAPERS.items():
        try:
            items = fn(today)
            if not items:
                raise ValueError("prázdný výsledek")
            status[chain] = {"source": "přímo", "count": len(items)}
            all_items.extend(items)
        except Exception as exc:
            backup = backup_by_chain.get(chain, [])
            status[chain] = {"source": "záloha", "count": len(backup),
                             "error": f"{type(exc).__name__}: {exc}"}
            all_items.extend(backup)
            print(f"{chain}: CHYBA – {exc}", file=sys.stderr)
            traceback.print_exc()
        time.sleep(1.0)

    for chain in FALLBACK_ONLY_CHAINS:
        items = backup_by_chain.get(chain, [])
        status[chain] = {"source": "záloha", "count": len(items)}
        all_items.extend(items)

    # deduplikace a řazení
    unique = {}
    for it in all_items:
        key = (it["name"], it["shop"], it["price"], it["volume"])
        unique.setdefault(key, it)
    result = list(unique.values())
    for it in result:
        it["brand"] = brand_of(it["name"])
    result.sort(key=lambda x: (x["price_per_l"] is None,
                               x["price_per_l"] or 0, x["price"]))

    print("Zdroje dat (řetězec: zdroj, počet):")
    for chain, st in status.items():
        print(f"  {chain}: {st['source']}, {st['count']} akcí")

    if not result:
        print("Nenalezeny žádné akce – data se nepřepisují.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "updated": today.isoformat(),
        "count": len(result),
        "sources": status,
        "items": result,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "history").mkdir(exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    out = DATA_DIR / "pivo.json"
    out.write_text(text, encoding="utf-8")
    (DATA_DIR / "history" / f"{today.isoformat()}.json").write_text(
        text, encoding="utf-8")
    print(f"\nUloženo {len(result)} akcí do {out}")


if __name__ == "__main__":
    main()
