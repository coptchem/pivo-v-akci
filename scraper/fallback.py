# -*- coding: utf-8 -*-
"""Záložní zdroj dat pro řetězce bez vlastního scraperu nebo při jeho selhání.

Implementace záložního zdroje není součástí repozitáře. Pokud existuje
soubor scraper/private/fallback_source.py s funkcí fetch(today), použije
se; jinak záloha vrací prázdný seznam a web zobrazí jen přímá data.
"""

import importlib.util
import sys
import traceback
from pathlib import Path

from utils import chain_of

SOURCE_FILE = Path(__file__).resolve().parent / "private" / "fallback_source.py"


def load_items(today):
    if not SOURCE_FILE.exists():
        print("Záložní zdroj není k dispozici – přeskakuji.")
        return []
    try:
        spec = importlib.util.spec_from_file_location("fallback_source", SOURCE_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        items = mod.fetch(today) or []
    except Exception as exc:  # záloha nesmí shodit celý běh
        print(f"Záložní zdroj selhal: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return []
    for it in items:
        it.setdefault("chain", chain_of(it.get("shop", "")))
        it.pop("url", None)
    return items
