# -*- coding: utf-8 -*-
"""Albert – extrakce piva z obrázkových letáků přes Claude API (Haiku 5.5).

Albert strukturovaná data nepublikuje; letáky jsou PDF na Publitas.
Postup: seznam letáků z albert.cz (__NEXT_DATA__) -> stažení PDF ->
render stránek -> Claude Haiku vytáhne akční piva jako JSON
(structured outputs – odpověď je vždy validní JSON podle OUTPUT_SCHEMA).

Výsledky se cachují v data/cache/albert/<id_letaku>.json (letáky se mění
týdně, API se tak volá jen pro nové letáky). Bez ANTHROPIC_API_KEY modul
selže a scrape.py použije záložní zdroj.
"""

import base64
import io
import json
import os
import re
import time
from pathlib import Path

from chains.common import HEADERS, get_session, make_item, parse_volume_l

LEAFLETS_URL = "https://www.albert.cz/aktualni-letaky"
MODEL = "claude-haiku-5-5"

CACHE_DIR = (Path(__file__).resolve().parent.parent.parent
             / "data" / "cache" / "albert")

RENDER_SCALE = 1.4   # ~675 px šířka stránky, ceny čitelné, malé tokeny
API_DELAY_S = 0.3

PROMPT = (
    "Na obrázku je stránka letáku supermarketu Albert. Vypiš VŠECHNY akční "
    "nabídky piva na této stránce (včetně nealkoholických piv a radlerů; "
    "cidery vynech). U každé nabídky uveď: name = název včetně značky; "
    "price = akční cena v Kč; old_price = původní cena, pokud je uvedena; "
    "volume = objem, např. \"0,5 l\" nebo \"6× 0,5 l\"; price_per_l = cena "
    "za litr, pokud je uvedena; note = podmínka akce (např. s aplikací, od "
    "čtvrtka, cena za kus při koupi X kusů). Co na stránce není, dej null. "
    "Pokud na stránce žádné pivo není, vrať prázdný seznam offers."
)

_NUM_OR_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_STR_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "offers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price": {"type": "number"},
                    "old_price": _NUM_OR_NULL,
                    "volume": _STR_OR_NULL,
                    "price_per_l": _NUM_OR_NULL,
                    "note": _STR_OR_NULL,
                },
                "required": ["name", "price", "old_price", "volume",
                             "price_per_l", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["offers"],
    "additionalProperties": False,
}


def _get_leaflets(s):
    r = s.get(LEAFLETS_URL, headers=HEADERS, timeout=40)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">'
                  r'(.*?)</script>', r.text, re.S)
    data = json.loads(m.group(1))

    leaflets = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("__typename") == "Leaflet" and o.get("downloadUrl"):
                leaflets.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    # unikátní podle id
    return list({lf["id"]: lf for lf in leaflets}.values())


def _cz_date_to_iso(text):
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", (text or "").strip())
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" \
        if m else None


def _render_pages(pdf_bytes):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_bytes)
    for page in pdf:
        img = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=78)
        yield buf.getvalue()


def _ask_claude(client, jpeg_bytes):
    """Vrátí seznam nabídek piva z jedné stránky letáku."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,   # rezerva i pro (krátké) přemýšlení modelu
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(jpeg_bytes).decode()}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    if response.stop_reason != "end_turn":
        # refusal / max_tokens – stránku přeskočíme, ostatní pokračují
        print(f"Albert: stránka přeskočena (stop_reason={response.stop_reason})")
        return []
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text).get("offers", [])
    except ValueError:
        return []


def _extract_leaflet(s, client, leaflet):
    pdf = s.get(leaflet["downloadUrl"], headers=HEADERS, timeout=180)
    pdf.raise_for_status()
    found = []
    for jpeg in _render_pages(pdf.content):
        for raw in _ask_claude(client, jpeg):
            if not isinstance(raw, dict):
                continue
            price = raw.get("price")
            if not isinstance(price, (int, float)) or not 0 < price < 2000:
                continue
            found.append(raw)
        time.sleep(API_DELAY_S)
    return found


def scrape(today):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY není nastaven")
    import anthropic
    client = anthropic.Anthropic(max_retries=4, timeout=120)
    s = get_session()
    leaflets = _get_leaflets(s)
    if not leaflets:
        raise ValueError("žádné letáky nenalezeny")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # cache letáků, které už albert.cz nenabízí, se maže
    current = {f"{lf['id']}.json" for lf in leaflets}
    for old in CACHE_DIR.glob("*.json"):
        if old.name not in current:
            old.unlink()

    # (name, price) -> {raw, leaflet info, locationTypes}
    grouped = {}
    for lf in leaflets:
        cache_file = CACHE_DIR / f"{lf['id']}.json"
        if cache_file.exists():
            raws = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            raws = _extract_leaflet(s, client, lf)
            cache_file.write_text(
                json.dumps(raws, ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(f"Albert: leták {lf['title']}: {len(raws)} piv (nové)")
        for raw in raws:
            key = (str(raw.get("name", "")).lower(), raw.get("price"))
            entry = grouped.setdefault(key, {"raw": raw, "lf": lf,
                                             "types": set()})
            entry["types"].add(lf.get("locationType"))

    items = []
    for entry in grouped.values():
        raw, lf = entry["raw"], entry["lf"]
        notes = []
        if raw.get("note"):
            notes.append(str(raw["note"]))
        if raw.get("old_price"):
            notes.append(f"původně {raw['old_price']:.2f} Kč"
                         .replace(".", ","))
        if entry["types"] == {"HYPERMARKET"}:
            notes.append("jen hypermarkety")
        elif entry["types"] == {"SUPERMARKET"}:
            notes.append("jen supermarkety")

        volume = raw.get("volume")
        per_l = raw.get("price_per_l")
        items.append(make_item(
            name=str(raw.get("name", "")),
            price=raw["price"],
            chain="Albert",
            volume=volume,
            volume_l=parse_volume_l(volume or ""),
            price_per_l=per_l if isinstance(per_l, (int, float)) else None,
            valid_from=_cz_date_to_iso(lf.get("validityStartDateFormatted")),
            valid_to=_cz_date_to_iso(lf.get("validityEndDateFormatted")),
            note="; ".join(notes) or None,
        ))
    return items
