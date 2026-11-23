# -*- coding: utf-8 -*-
"""Společná extrakce akčních piv z obrázků stránek letáků přes Claude API.

Používají ji řetězce, které akce piva zveřejňují jen v letácích
(Albert, Lidl, Tesco). Model vrací strukturovaný JSON podle OUTPUT_SCHEMA
(structured outputs – odpověď je vždy validní JSON).

Výsledky se cachují v data/cache/<řetězec>/<id_letáku>.json, API se tak
volá jen pro nové letáky.
"""

import base64
import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = "claude-haiku-5-5"
WORKERS = 4            # souběžné dotazy na API
TARGET_WIDTH_PX = 1000  # šířka renderu PDF stránky – ceny čitelné, málo tokenů

CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

PROMPT = (
    "Na obrázku je stránka letáku supermarketu {chain}. Vypiš VŠECHNY akční "
    "nabídky piva na této stránce (včetně nealkoholických piv a radlerů; "
    "cidery, limonády a pivní sklenice vynech). U každé nabídky uveď: "
    "name = název včetně značky; price = akční cena v Kč (u věrnostní ceny, "
    "např. s aplikací nebo kartou, uveď tuto cenu a podmínku dej do note); "
    "old_price = původní cena, pokud je uvedena; volume = objem, např. "
    "\"0,5 l\" nebo \"6× 0,5 l\"; price_per_l = cena za litr, pokud je "
    "uvedena; note = podmínka akce (např. s aplikací, cena za kus při koupi "
    "X kusů); valid_from / valid_to = platnost akce ve formátu YYYY-MM-DD, "
    "pokud je na stránce uvedena (rok {year}). Co na stránce není, dej null. "
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
                    "valid_from": _STR_OR_NULL,
                    "valid_to": _STR_OR_NULL,
                },
                "required": ["name", "price", "old_price", "volume",
                             "price_per_l", "note", "valid_from", "valid_to"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["offers"],
    "additionalProperties": False,
}

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def make_client():
    """Klient Claude API; bez klíče výjimka (scrape.py pak použije zálohu)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY není nastaven")
    import anthropic
    return anthropic.Anthropic(max_retries=4, timeout=120)


def render_pdf(pdf_bytes, quality=80):
    """Vyrenderuje všechny stránky PDF do JPEG (šířka ~TARGET_WIDTH_PX)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_bytes)
    pages = []
    for page in pdf:
        scale = min(2.0, TARGET_WIDTH_PX / max(page.get_width(), 1))
        img = page.render(scale=scale).to_pil().convert("RGB")
        pages.append(to_jpeg(img, quality))
    return pages


def to_jpeg(img, quality=80):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def shrink_jpeg(data, max_width=TARGET_WIDTH_PX * 1.3, quality=80):
    """Zmenší stažený obrázek stránky (šetří tokeny i přenos)."""
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.width > max_width:
        h = round(img.height * max_width / img.width)
        img = img.resize((int(max_width), h), Image.LANCZOS)
    return to_jpeg(img, quality)


def _clean_offer(raw):
    if not isinstance(raw, dict):
        return None
    price = raw.get("price")
    if not isinstance(price, (int, float)) or not 0 < price < 2000:
        return None
    for k in ("valid_from", "valid_to"):
        v = raw.get(k)
        if not (isinstance(v, str) and _ISO_RE.match(v)):
            raw[k] = None
    return raw


def ask_page(client, jpeg_bytes, chain, year):
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
                {"type": "text",
                 "text": PROMPT.format(chain=chain, year=year)},
            ],
        }],
    )
    if response.stop_reason != "end_turn":
        # refusal / max_tokens – stránku přeskočíme, ostatní pokračují
        print(f"{chain}: stránka přeskočena "
              f"(stop_reason={response.stop_reason})")
        return []
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        offers = json.loads(text).get("offers", [])
    except ValueError:
        return []
    return [o for o in map(_clean_offer, offers) if o]


def extract_pages(client, pages, chain, year):
    """Projde stránky (JPEG bytes nebo funkce vracející JPEG) souběžně."""
    def one(page):
        data = page() if callable(page) else page
        return ask_page(client, data, chain, year)

    with ThreadPoolExecutor(WORKERS) as ex:
        return [o for offers in ex.map(one, pages) for o in offers]


class LeafletCache:
    """Cache výsledků po letácích: data/cache/<chain>/<id>.json."""

    def __init__(self, chain):
        self.dir = CACHE_ROOT / chain.lower()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, leaflet_id):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(leaflet_id))
        return self.dir / f"{safe}.json"

    def get(self, leaflet_id):
        p = self._path(leaflet_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def put(self, leaflet_id, offers):
        self._path(leaflet_id).write_text(
            json.dumps(offers, ensure_ascii=False, indent=1),
            encoding="utf-8")

    def prune(self, current_ids):
        """Smaže cache letáků, které už řetězec nenabízí."""
        keep = {self._path(i).name for i in current_ids}
        for old in self.dir.glob("*.json"):
            if old.name not in keep:
                old.unlink()


def price_note(raw):
    """Poznámka z extrahované nabídky (podmínka + původní cena)."""
    notes = []
    if raw.get("note"):
        notes.append(str(raw["note"]))
    old = raw.get("old_price")
    if isinstance(old, (int, float)) and old > raw["price"]:
        notes.append(f"původně {old:.2f} Kč".replace(".", ","))
    return notes
