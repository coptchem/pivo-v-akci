#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rozešle odběratelům e-mail s novými akcemi podle jejich preferencí.

Běží v GitHub Actions po scrape.py. Nové akce = položky v dnešním
data/pivo.json, které nebyly v posledním předchozím snapshotu
v data/history/. Odběratelé se stahují z api/export.php (tajný token
v hlavičce X-Export-Token), posílá se přes SMTP schránku hostingu.

Vyžaduje env: SITE_URL, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
EXPORT_TOKEN (volitelně MAIL_FROM, jinak SMTP_USER). Bez nich skript jen
vypíše hlášku a skončí úspěšně. Za den se rozesílá nejvýš jednou
(data/digest_state.json), opakované ruční spuštění workflow nic nepošle.

Pozor: logy GitHub Actions jsou u veřejného repozitáře veřejné – skript
proto nikdy nevypisuje e-mailové adresy.
"""

import json
import os
import smtplib
import sys
import time
from datetime import date
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chains.brands import brand_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "digest_state.json"
MAX_ITEMS_PER_MAIL = 40


def item_key(it):
    """Klíč akce nezávislý na zdroji dat (přímý scraper a záloha pojmenují
    stejné pivo různě) – jinak by přepnutí zdroje vypadalo jako nové akce."""
    return (it["chain"], it.get("brand") or it["name"].lower(),
            round(float(it["price"]), 2), it.get("volume_l"))


def _with_brands(items):
    for it in items:
        if not it.get("brand"):
            it["brand"] = brand_of(it["name"])
    return items


def load_new_items(today):
    """Dnešní položky, které nebyly v posledním starším snapshotu."""
    current = json.loads((DATA_DIR / "pivo.json").read_text(encoding="utf-8"))
    items = _with_brands(current["items"])

    previous = sorted(
        p for p in (DATA_DIR / "history").glob("*.json")
        if p.stem < today.isoformat())
    if not previous:
        # první běh – nemáme s čím porovnat, radši neposílat nic
        return []
    old = json.loads(previous[-1].read_text(encoding="utf-8"))
    old_keys = {item_key(it) for it in _with_brands(old["items"])}
    return [it for it in items if item_key(it) not in old_keys]


def matches(sub, it):
    if sub["chains"] and it["chain"] not in sub["chains"]:
        return False
    if sub["brands"] and it.get("brand") not in sub["brands"]:
        return False
    return True


def format_item(it):
    line = f"• {it['name']} – {it['shop']}: {it['price']:.2f} Kč"
    if it.get("volume"):
        line += f" / {it['volume']}"
    if it.get("price_per_l"):
        line += f" ({it['price_per_l']:.2f} Kč/l)"
    extras = [x for x in (it.get("validity_text"), it.get("note")) if x]
    if extras:
        line += "\n   " + ", ".join(extras)
    return line


def build_mail(sub, its, site_url):
    its = sorted(its, key=lambda x: (x["price_per_l"] is None,
                                     x["price_per_l"] or 0, x["price"]))
    shown = its[:MAX_ITEMS_PER_MAIL]
    body = "Dobrý den,\n\nod posledně přibyly tyto akce podle vašeho výběru:\n\n"
    body += "\n\n".join(format_item(it) for it in shown)
    if len(its) > len(shown):
        body += f"\n\n… a dalších {len(its) - len(shown)} akcí na webu."
    body += (
        f"\n\nVšechny akce: {site_url}\n\n"
        "--\n"
        "Pivo v akci – upozornění chodí, jen když se objeví něco nového.\n"
        f"Změnit výběr: {site_url}/#upozorneni\n"
        f"Odhlásit odběr: {site_url}/api/unsubscribe.php?t={sub['token']}\n")
    return body


def already_sent(today):
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return state.get("last_sent") == today.isoformat()


def mark_sent(today):
    STATE_FILE.write_text(json.dumps({"last_sent": today.isoformat()}),
                          encoding="utf-8")


def main():
    cfg = {k: os.environ.get(k, "") for k in
           ("SITE_URL", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
            "EXPORT_TOKEN")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        print(f"Digest přeskočen – chybí secrets: {', '.join(missing)}")
        return
    site_url = cfg["SITE_URL"].rstrip("/")
    mail_from = os.environ.get("MAIL_FROM") or cfg["SMTP_USER"]

    today = date.today()
    if already_sent(today):
        print("Digest už dnes proběhl – přeskakuji.")
        return

    new_items = load_new_items(today)
    print(f"Nových akcí od posledního běhu: {len(new_items)}")
    if not new_items:
        mark_sent(today)
        return

    resp = requests.get(f"{site_url}/api/export.php",
                        headers={"X-Export-Token": cfg["EXPORT_TOKEN"]},
                        timeout=30)
    resp.raise_for_status()
    subscribers = resp.json()["subscribers"]
    print(f"Potvrzených odběratelů: {len(subscribers)}")

    to_send = []
    for sub in subscribers:
        matched = [it for it in new_items if matches(sub, it)]
        if matched:
            to_send.append((sub, matched))
    print(f"E-mailů k odeslání: {len(to_send)}")
    if not to_send:
        mark_sent(today)
        return

    port = int(cfg["SMTP_PORT"])
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=30)
    else:
        server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=30)
        server.starttls()
    server.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])

    sent = failed = 0
    domain = mail_from.rsplit("@", 1)[-1]
    with server:
        for sub, matched in to_send:
            msg = MIMEText(build_mail(sub, matched, site_url), "plain", "utf-8")
            msg["Subject"] = Header(
                f"🍺 {len(matched)} nových akcí na pivo", "utf-8")
            msg["From"] = formataddr(("Pivo v akci", mail_from))
            msg["To"] = sub["email"]
            msg["Message-ID"] = make_msgid(domain=domain)
            unsub = f"{site_url}/api/unsubscribe.php?t={sub['token']}"
            msg["List-Unsubscribe"] = f"<{unsub}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            try:
                server.sendmail(mail_from, [sub["email"]], msg.as_string())
                sent += 1
            except smtplib.SMTPException as exc:
                failed += 1
                # bez adresy příjemce – logy Actions jsou veřejné
                print(f"CHYBA odeslání: {type(exc).__name__}", file=sys.stderr)
            time.sleep(0.5)

    mark_sent(today)
    print(f"Odesláno {sent} e-mailů, selhalo {failed}.")


if __name__ == "__main__":
    main()
