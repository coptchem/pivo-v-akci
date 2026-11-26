#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluace vytěžování letáků na zlaté sadě.

    python scraper/eval/run_eval.py                    # legacy i graph
    python scraper/eval/run_eval.py --pipeline graph
    python scraper/eval/run_eval.py --all              # i neověřené stránky

Pro každou ověřenou stránku z golden/ spustí pipeline, spáruje nabídky
se zlatou sadou a vypíše precision, recall, shodu polí, počet volání
a tokeny podle modelu. Volá Claude API – stojí peníze.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chains import leaflet_ai, leaflet_graph  # noqa: E402
from metrics import compare, summarize  # noqa: E402

# GOLDEN_DIR: složka se zlatou sadou (v CI checkout soukromého repa)
GOLDEN = Path(os.environ.get("GOLDEN_DIR")
              or Path(__file__).resolve().parent / "golden")


class CountingClient:
    """Obal klienta, který si zapisuje model a tokeny každého volání."""

    def __init__(self, client):
        self._client = client
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        response = self._client.messages.create(**kwargs)
        self.calls.append({
            "model": kwargs["model"],
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        })
        return response


def run_legacy(client, jpeg, chain, year):
    return leaflet_ai.ask_page(client, jpeg, chain, year)


def run_graph(client, jpeg, chain, year):
    graph = leaflet_graph.build_graph(client)
    return leaflet_graph.run_page(graph, jpeg, chain, year)["offers"]


PIPELINES = {"legacy": run_legacy, "graph": run_graph}


def load_golden(include_unverified):
    labels = []
    for p in sorted(GOLDEN.glob("*.json")):
        label = json.loads(p.read_text(encoding="utf-8"))
        if label.get("verified") or include_unverified:
            labels.append((p.stem, label))
    return labels


def evaluate(name, labels, base_client):
    client = CountingClient(base_client)
    rows, started = [], time.monotonic()
    for page_id, label in labels:
        jpeg = (GOLDEN / label["image"]).read_bytes()
        predicted = PIPELINES[name](client, jpeg, label["chain"], label["year"])
        row = compare(label["offers"], predicted)
        rows.append(row)
        if row["missed"] or row["extra"]:
            print(f"  [{name}] {page_id}: chybí {row['missed']}, "
                  f"navíc {row['extra']}")
    result = summarize(rows)
    result["seconds"] = round(time.monotonic() - started, 1)
    per_model = {}
    for c in client.calls:
        m = per_model.setdefault(c["model"], {"calls": 0, "input_tokens": 0,
                                              "output_tokens": 0})
        m["calls"] += 1
        m["input_tokens"] += c["input_tokens"]
        m["output_tokens"] += c["output_tokens"]
    result["usage"] = per_model
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", choices=["legacy", "graph", "both"],
                    default="both")
    ap.add_argument("--all", action="store_true",
                    help="použít i neověřené stránky (jen pro ladění)")
    ap.add_argument("--out", type=Path, help="uložit výsledky jako JSON")
    args = ap.parse_args()

    labels = load_golden(args.all)
    if not labels:
        sys.exit(f"V {GOLDEN} nejsou žádné ověřené stránky "
                 f"(viz build_golden.py).")
    names = ["legacy", "graph"] if args.pipeline == "both" else [args.pipeline]
    base_client = leaflet_ai.make_client()

    results = {n: evaluate(n, labels, base_client) for n in names}

    print(f"\nStránek: {len(labels)}")
    print(f"{'pipeline':<8} {'precision':>9} {'recall':>7} {'objem':>6} "
          f"{'pův.cena':>8} {'Kč/l':>6} {'čas s':>6}  volání a tokeny")
    for n, r in results.items():
        fa = r["field_accuracy"]
        usage = "; ".join(f"{m}: {u['calls']}× {u['input_tokens']}/"
                          f"{u['output_tokens']}" for m, u in r["usage"].items())
        print(f"{n:<8} {r['precision']:>9.1%} {r['recall']:>7.1%} "
              f"{fa['volume']:>6.0%} {fa['old_price']:>8.0%} "
              f"{fa['price_per_l']:>6.0%} {r['seconds']:>6}  {usage}")
    if args.out:
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                            encoding="utf-8")


if __name__ == "__main__":
    main()
