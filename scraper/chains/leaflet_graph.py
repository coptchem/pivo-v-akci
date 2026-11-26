# -*- coding: utf-8 -*-
"""Vytěžování akčních piv ze stránky letáku jako LangGraph graf.

Jedno volání modelu (chains/leaflet_ai) občas přečte cenu špatně: zamění
cenu za kus a za balení, přehlédne, že cena za litr patří k jiné nabídce,
nebo vrátí původní cenu nižší než akční. Graf proto výsledek kontroluje
a chyby opravuje:

    START -> extract -> validate -+-> finish -> END            (bez problémů)
                ^                 +-> retry ----> extract      (znovu, se zpětnou vazbou)
                |                 +-> escalate -> extract      (silnější model)
                +-----------------+-> repair -> finish -> END  (deterministická oprava)

- validate: deterministická pravidla (rozsah ceny, cena za litr vs. cena
  a objem, původní cena > akční, platnost), žádné volání modelu
- retry: stejný model dostane svou předchozí odpověď a seznam problémů
- escalate: po MAX_ATTEMPTS pokusech jeden pokus silnějším modelem
- repair: co se nepodařilo opravit, se opraví pravidly (nesmyslná cena ->
  nabídka zahozena, nesedící cena za litr -> dopočítá se z objemu) a zapíše
  do fronty k ruční kontrole (data/review/)

Graf běží pro každou stránku zvlášť, stránky souběžně (WORKERS). Cache po
letácích zůstává v jednotlivých řetězcích, graf se tak spouští jen pro
nové letáky.

Diagram grafu: python scraper/chains/leaflet_graph.py --mermaid
"""

import json
import operator
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

if __package__ in (None, ""):  # spuštění jako skript (--mermaid)
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from chains import leaflet_ai  # noqa: E402
from utils import parse_volume_l  # noqa: E402

BASE_MODEL = leaflet_ai.MODEL
STRONG_MODEL = os.environ.get("LEAFLET_STRONG_MODEL", "claude-sonnet-5-5")
MAX_ATTEMPTS = 2          # pokusy základním modelem (první + 1 oprava)
PER_LITRE_TOLERANCE = 0.12  # relativní odchylka ceny za litr od price/objem

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FEEDBACK = (
    "Automatická kontrola tvé odpovědi našla tyto problémy:\n{issues}\n\n"
    "Podívej se znovu pozorně na stránku, hlavně na ceny, objemy a ceny za "
    "litr, a vrať opravený KOMPLETNÍ seznam nabídek ze stránky. Pokud je "
    "údaj na stránce opravdu uveden tak, jak jsi ho vrátil, ponech ho."
)


# ---------------------------------------------------------------- kontrola

def check_offer(raw, year):
    """Problémy jedné nabídky jako [(kód, popis)]; prázdný seznam = v pořádku."""
    issues = []
    name = str(raw.get("name") or "?")
    price = raw.get("price")
    if not isinstance(price, (int, float)) or not 0 < price < 2000:
        return [("price", f"„{name}“: nesmyslná akční cena {price!r}")]

    old = raw.get("old_price")
    if isinstance(old, (int, float)) and old <= price:
        issues.append(("old_price", f"„{name}“: původní cena {old} Kč není "
                                    f"vyšší než akční cena {price} Kč"))

    volume = raw.get("volume")
    volume_l = parse_volume_l(volume) if isinstance(volume, str) else None
    if volume_l is not None and not 0.2 <= volume_l <= 30:
        issues.append(("volume", f"„{name}“: podezřelý objem {volume!r}"))
        volume_l = None

    per_l = raw.get("price_per_l")
    if isinstance(per_l, (int, float)) and volume_l:
        expected = price / volume_l
        if abs(per_l - expected) / expected > PER_LITRE_TOLERANCE:
            issues.append(("price_per_l",
                           f"„{name}“: cena za litr {per_l} Kč neodpovídá ceně "
                           f"{price} Kč za {volume} (vychází "
                           f"{expected:.2f} Kč/l)"))

    dates = {}
    for k in ("valid_from", "valid_to"):
        v = raw.get(k)
        if isinstance(v, str) and _ISO_RE.match(v):
            if abs(int(v[:4]) - year) > 1:
                issues.append(("dates", f"„{name}“: nepravděpodobný rok v {k} {v}"))
            else:
                dates[k] = v
    if len(dates) == 2 and dates["valid_from"] > dates["valid_to"]:
        issues.append(("dates", f"„{name}“: platnost od {dates['valid_from']} "
                                f"je po platnosti do {dates['valid_to']}"))
    return issues


def repair_offer(raw, codes):
    """Deterministická oprava; None = nabídku zahodit."""
    if "price" in codes:
        return None
    fixed = dict(raw)
    if "old_price" in codes:
        fixed["old_price"] = None
    if "volume" in codes:
        fixed["volume"] = None
        fixed["price_per_l"] = None
    if "price_per_l" in codes:
        fixed["price_per_l"] = None  # make_item ji dopočítá z objemu
    if "dates" in codes:
        fixed["valid_from"] = fixed["valid_to"] = None
    return fixed


# ---------------------------------------------------------------- graf

class PageState(TypedDict, total=False):
    chain: str
    year: int
    image: bytes
    model: str
    attempts: int          # počet volání modelu
    messages: list         # konverzace s modelem (obrázek, odpovědi, feedback)
    offers: list           # poslední surové nabídky
    issues: list           # [(index nabídky, kód, popis)] z poslední kontroly
    review: list           # záznamy k ruční kontrole
    calls: Annotated[list, operator.add]  # usage každého volání


def build_graph(client):
    def extract(state: PageState):
        messages = state.get("messages") or leaflet_ai.page_messages(
            state["image"], state["chain"], state["year"])
        offers, stop, usage = leaflet_ai.request_offers(
            client, messages, model=state["model"],
            effort="low" if state["model"] == BASE_MODEL else "medium")
        update = {"attempts": state.get("attempts", 0) + 1,
                  "messages": messages, "calls": [usage]}
        if offers is None:
            update["offers"] = []
            update["issues"] = [(None, "stop",
                                 f"odpověď nešla použít (stop_reason={stop})")]
        else:
            update["offers"] = offers
            update["issues"] = None
        return update

    def validate(state: PageState):
        if state.get("issues"):  # nepoužitelná odpověď z extract
            return {}
        issues = [(i, code, text)
                  for i, raw in enumerate(state["offers"])
                  for code, text in check_offer(raw, state["year"])]
        return {"issues": issues}

    def route(state: PageState):
        if not state.get("issues"):
            return "finish"
        if state["attempts"] < MAX_ATTEMPTS:
            return "retry"
        if state["model"] != STRONG_MODEL and STRONG_MODEL:
            return "escalate"
        return "repair"

    def with_feedback(state: PageState):
        """Předchozí odpověď + seznam problémů jako další kolo konverzace."""
        issues = "\n".join(f"- {text}" for _, _, text in state["issues"])
        previous = json.dumps({"offers": state["offers"]}, ensure_ascii=False)
        messages = list(state["messages"])
        if state["offers"] or any(c != "stop" for _, c, _ in state["issues"]):
            messages += [
                {"role": "assistant", "content": previous},
                {"role": "user", "content": FEEDBACK.format(issues=issues)},
            ]
        return messages

    def retry(state: PageState):
        return {"messages": with_feedback(state)}

    def escalate(state: PageState):
        return {"messages": with_feedback(state), "model": STRONG_MODEL}

    def repair(state: PageState):
        by_offer = {}
        for i, code, text in state["issues"]:
            by_offer.setdefault(i, []).append((code, text))
        offers, review = [], []
        for i, raw in enumerate(state["offers"]):
            problems = by_offer.get(i)
            if not problems:
                offers.append(raw)
                continue
            fixed = repair_offer(raw, {c for c, _ in problems})
            review.append({
                "chain": state["chain"],
                "offer": raw,
                "issues": [t for _, t in problems],
                "action": "zahozeno" if fixed is None else "opraveno pravidly",
            })
            if fixed is not None:
                offers.append(fixed)
        if None in by_offer:  # model opakovaně nevrátil použitelnou odpověď
            review.append({"chain": state["chain"], "offer": None,
                           "issues": [t for _, t in by_offer[None]],
                           "action": "stránka přeskočena"})
        return {"offers": offers, "review": review}

    def finish(state: PageState):
        cleaned = [o for o in map(leaflet_ai._clean_offer, state["offers"]) if o]
        return {"offers": cleaned}

    g = StateGraph(PageState)
    g.add_node("extract", extract)
    g.add_node("validate", validate)
    g.add_node("retry", retry)
    g.add_node("escalate", escalate)
    g.add_node("repair", repair)
    g.add_node("finish", finish)
    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", route, {
        "finish": "finish", "retry": "retry",
        "escalate": "escalate", "repair": "repair"})
    g.add_edge("retry", "extract")
    g.add_edge("escalate", "extract")
    g.add_edge("repair", "finish")
    g.add_edge("finish", END)
    return g.compile()


def run_page(graph, jpeg_bytes, chain, year):
    """Spustí graf pro jednu stránku; vrací konečný stav."""
    return graph.invoke({"chain": chain, "year": year, "image": jpeg_bytes,
                         "model": BASE_MODEL, "attempts": 0, "calls": []})


# ---------------------------------------------------------------- report

_lock = threading.Lock()
_REVIEW = []
_CALLS = []


def drain_report():
    """Vrátí a vynuluje frontu k ruční kontrole a usage od posledního volání."""
    with _lock:
        review, calls = list(_REVIEW), list(_CALLS)
        _REVIEW.clear()
        _CALLS.clear()
    return review, calls


def extract_pages(client, pages, chain, year):
    """Stejné rozhraní jako leaflet_ai.extract_pages_legacy."""
    graph = build_graph(client)

    def one(page):
        data = page() if callable(page) else page
        state = run_page(graph, data, chain, year)
        with _lock:
            _REVIEW.extend(state.get("review") or [])
            _CALLS.extend(state.get("calls") or [])
        if state["attempts"] > 1:
            print(f"{chain}: stránka potřebovala {state['attempts']} volání "
                  f"({', '.join(c['model'] for c in state['calls'])})")
        return state["offers"]

    with ThreadPoolExecutor(leaflet_ai.WORKERS) as ex:
        return [o for offers in ex.map(one, pages) for o in offers]


if __name__ == "__main__":
    if "--mermaid" in sys.argv:
        print(build_graph(None).get_graph().draw_mermaid())
