#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Týdenní souhrn nákladů na provoz (Claude API za vytěžování letáků).

Sečte data/usage/ za posledních 7 dní (včetně dneška) a za aktuální
měsíc, ocení tokeny podle ceníku v usage.py a přepočte na Kč kurzem ČNB.
Souhrn vypíše, zapíše do Job summary (GITHUB_STEP_SUMMARY) a pošle
e-mailem na REPORT_TO přes SMTP schránku hostingu (stejné secrets jako
send_digest.py). Bez REPORT_TO nebo SMTP se e-mail jen přeskočí.

Je to odhad z tokenů, které vrátilo API, ne faktura. GitHub Actions je
u veřejného repozitáře zdarma; hosting a doména v souhrnu nejsou.
Logy Actions jsou veřejné, adresa příjemce se proto nikdy nevypisuje.

    python scraper/cost_report.py [--days 7] [--no-mail]
"""

import argparse
import json
import os
import smtplib
import sys
from datetime import date, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CNB_URL = ("https://www.cnb.cz/cs/financni-trhy/devizovy-trh/"
           "kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt")


def usd_czk():
    """Kurz USD/CZK z denního kurzovního lístku ČNB; None při chybě."""
    try:
        text = requests.get(CNB_URL, timeout=20).text
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) == 5 and parts[3] == "USD":
                return float(parts[4].replace(",", ".")) / int(parts[2])
    except (requests.RequestException, ValueError):
        pass
    return None


def load_rows(first, last):
    """Řádky usage za dny first..last a počet dní, kdy API běželo."""
    rows, days = [], 0
    d = first
    while d <= last:
        p = DATA_DIR / "usage" / f"{d.isoformat()}.json"
        if p.exists():
            day = json.loads(p.read_text(encoding="utf-8"))["rows"]
            rows += day
            days += bool(day)
        d += timedelta(days=1)
    return rows, days


def group(rows, key):
    out = {}
    for r in rows:
        g = out.setdefault(r[key], {"calls": 0, "pages": 0, "input_tokens": 0,
                                    "output_tokens": 0, "usd": 0.0})
        for k in ("calls", "pages", "input_tokens", "output_tokens"):
            g[k] += r[k]
        g["usd"] += usage.cost_usd(r) or 0.0
    return out


def money(usd, rate):
    if rate is None:
        return f"{usd:.3f} USD"
    return f"{usd * rate:.2f} Kč".replace(".", ",") + f" ({usd:.3f} USD)"


def build_report(today, days, rate):
    first = today - timedelta(days=days - 1)
    rows, active = load_rows(first, today)
    month_rows, _ = load_rows(today.replace(day=1), today)
    total = sum(usage.cost_usd(r) or 0.0 for r in rows)
    month = sum(usage.cost_usd(r) or 0.0 for r in month_rows)
    unknown = sorted({r["model"] for r in rows if usage.cost_usd(r) is None})

    lines = [
        f"Pivo v akci: náklady za {first:%d. %m.} až {today:%d. %m. %Y}",
        "",
        f"Celkem za {days} dní: {money(total, rate)}",
        f"Od začátku měsíce: {money(month, rate)}",
        f"Dny s novými letáky: {active} z {days}",
    ]
    if rows:
        lines += ["", "Podle řetězce (stránky, volání, cena):"]
        for chain, g in sorted(group(rows, "chain").items()):
            lines.append(f"  {chain}: stránek {g['pages']}, volání {g['calls']},"
                         f" {money(g['usd'], rate)}")
        lines += ["", "Podle modelu (volání, vstupní / výstupní tokeny, cena):"]
        for model, g in sorted(group(rows, "model").items()):
            lines.append(f"  {model}: {g['calls']}, {g['input_tokens']} / "
                         f"{g['output_tokens']}, {money(g['usd'], rate)}")
    else:
        lines += ["", "Žádná volání Claude API (žádné nové letáky)."]
    if unknown:
        lines += ["", f"Pozor: modely mimo ceník v usage.py, cena nezapočtena:"
                      f" {', '.join(unknown)}"]
    lines += [
        "",
        "Odhad z tokenů vrácených API, ne faktura." +
        (f" Kurz ČNB {rate:.3f} Kč/USD.".replace(".", ",", 1) if rate
         else " Kurz ČNB nebyl dostupný, ceny jsou v USD."),
        "GitHub Actions: zdarma (veřejný repozitář). Hosting a doména "
        "nejsou zahrnuty, ruční evaluace také ne.",
    ]
    return lines[0], "\n".join(lines) + "\n"


def send_mail(subject, body):
    cfg = {k: os.environ.get(k, "") for k in
           ("REPORT_TO", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        print(f"E-mail přeskočen, chybí: {', '.join(missing)}")
        return
    mail_from = os.environ.get("MAIL_FROM") or cfg["SMTP_USER"]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("Pivo v akci", mail_from))
    msg["To"] = cfg["REPORT_TO"]
    msg["Message-ID"] = make_msgid(domain=mail_from.rsplit("@", 1)[-1])
    port = int(cfg["SMTP_PORT"])
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=30)
    else:
        server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=30)
        server.starttls()
    with server:
        server.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
        server.sendmail(mail_from, [cfg["REPORT_TO"]], msg.as_string())
    print("Souhrn odeslán e-mailem.")  # bez adresy, logy jsou veřejné


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-mail", action="store_true")
    args = ap.parse_args()

    subject, body = build_report(date.today(), args.days, usd_czk())
    print(body)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"```\n{body}```\n")
    if not args.no_mail:
        send_mail(subject, body)


if __name__ == "__main__":
    main()
