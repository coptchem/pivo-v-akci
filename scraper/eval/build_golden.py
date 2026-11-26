#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Připraví kandidáty do zlaté sady: stránky letáku + návrh odpovědi.

    python scraper/eval/build_golden.py --albert --max-pages 30
    python scraper/eval/build_golden.py --pdf letak.pdf --chain Tesco

Každá stránka s pivem se uloží jako golden/<id>.jpg (jen lokálně, obrázky
letáků se necommitují) a golden/<id>.json s návrhem nabídek od silnějšího
modelu a "verified": false. Návrh je potřeba ručně zkontrolovat proti
obrázku, opravit a nastavit "verified": true; run_eval.py jinak stránku
nepoužije. Přidá se i pár stránek bez piva (negativní příklady).

Volá Claude API (silnější model, jedno volání na stránku) – stojí peníze.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chains import leaflet_ai  # noqa: E402
from chains.leaflet_graph import STRONG_MODEL  # noqa: E402

# GOLDEN_DIR: složka se zlatou sadou (v CI checkout soukromého repa)
GOLDEN = Path(os.environ.get("GOLDEN_DIR")
              or Path(__file__).resolve().parent / "golden")
EMPTY_PAGES = 3


def albert_pdfs(limit):
    from chains import albert
    from chains.common import HEADERS, get_session
    s = get_session()
    for lf in albert._get_leaflets(s)[:limit]:
        r = s.get(lf["downloadUrl"], headers=HEADERS, timeout=180)
        r.raise_for_status()
        yield "Albert", str(lf["id"]), r.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--albert", action="store_true",
                    help="aktuální letáky Albertu z albert.cz")
    ap.add_argument("--leaflets", type=int, default=1)
    ap.add_argument("--pdf", type=Path, help="lokální PDF letáku")
    ap.add_argument("--chain", default="Albert")
    ap.add_argument("--max-pages", type=int, default=30)
    args = ap.parse_args()

    if args.pdf:
        sources = [(args.chain, args.pdf.stem, args.pdf.read_bytes())]
    elif args.albert:
        sources = albert_pdfs(args.leaflets)
    else:
        ap.error("zadej --albert nebo --pdf")

    client = leaflet_ai.make_client()
    year = date.today().year
    GOLDEN.mkdir(exist_ok=True)
    for chain, leaflet_id, pdf in sources:
        empty = 0
        pages = leaflet_ai.render_pdf(pdf)[:args.max_pages]
        for n, jpeg in enumerate(pages, 1):
            page_id = f"{chain.lower()}-{leaflet_id}-p{n:02d}"
            existing = GOLDEN / f"{page_id}.json"
            if existing.exists() and json.loads(
                    existing.read_text(encoding="utf-8")).get("verified"):
                continue  # ověřenou stránku nepřepisovat (a neplatit za ni)
            offers, stop, _ = leaflet_ai.request_offers(
                client, leaflet_ai.page_messages(jpeg, chain, year),
                model=STRONG_MODEL, effort="medium")
            if offers is None:
                print(f"{chain} {leaflet_id} s. {n}: přeskočeno ({stop})")
                continue
            if not offers:
                if empty >= EMPTY_PAGES:
                    continue
                empty += 1
            (GOLDEN / f"{page_id}.jpg").write_bytes(jpeg)
            label = {"chain": chain, "year": year, "image": f"{page_id}.jpg",
                     "verified": False, "offers": offers}
            (GOLDEN / f"{page_id}.json").write_text(
                json.dumps(label, ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(f"{page_id}: {len(offers)} nabídek")
    print(f"\nZkontroluj návrhy v {GOLDEN} a u ověřených nastav "
          f'"verified": true.')


if __name__ == "__main__":
    main()
