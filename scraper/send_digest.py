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

import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import date
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chains.brands import BRAND_PATTERNS, brand_of  # noqa: E402

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


# barvy jako na webu (index.html)
C_BG, C_CARD, C_TEXT, C_MUTED, C_LINE, C_ACCENT, C_SOFT = (
    "#f6f4ef", "#ffffff", "#1f1c17", "#7a746a", "#e6e2d9", "#b8860b",
    "#fbf1d6")
CHAIN_COLORS = {
    "Kaufland": "#e10915", "Lidl": "#0050aa", "Penny": "#c8161d",
    "Albert": "#00ade6", "Billa": "#e30613", "Tesco": "#00539f",
    "Globus": "#f18700", "COOP": "#e8641c",
}
FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _kc(v):
    return f"{v:.2f}".replace(".", ",") + " Kč"


def _name_html(name):
    """Název piva se značkou tučně."""
    low = name.lower()
    for pat, _ in BRAND_PATTERNS:
        i = low.find(pat)
        if i >= 0:
            # tučně celé slovo, ve kterém značka je (např. "Svijanský")
            j = i + len(pat)
            while j < len(name) and name[j].isalpha():
                j += 1
            while i > 0 and name[i - 1].isalpha():
                i -= 1
            # i předchozí slovo s velkým písmenem ("Velkopopovický Kozel")
            m = re.search(r"(\w+)\s+$", name[:i])
            if m and m.group(1)[0].isupper():
                i = m.start(1)
            return (html.escape(name[:i]) + "<strong>" + html.escape(name[i:j])
                    + "</strong>" + html.escape(name[j:]))
    # neznámá značka – tučně první slovo
    first, _, rest = name.partition(" ")
    return ("<strong>" + html.escape(first) + "</strong>"
            + (" " + html.escape(rest) if rest else ""))


def _item_html(it):
    color = CHAIN_COLORS.get(it["chain"], C_MUTED)
    meta = [f'<span style="color:{color};font-weight:600">&#9679;</span> '
            + html.escape(it["shop"])]
    if it.get("volume"):
        meta.append(html.escape(it["volume"]))
    if it.get("validity_text"):
        meta.append(html.escape(it["validity_text"]))
    note = (f'<div style="font-size:12px;color:{C_MUTED};padding-top:2px">'
            f'{html.escape(it["note"])}</div>') if it.get("note") else ""
    per_l = (f'<div style="font-size:12px;color:{C_MUTED}">'
             f'{_kc(it["price_per_l"])}/l</div>') if it.get("price_per_l") else ""
    return (
        f'<tr><td style="padding:12px 0;border-top:1px solid {C_LINE};'
        f'vertical-align:top">'
        f'<div style="font-size:15px;color:{C_TEXT}">{_name_html(it["name"])}</div>'
        f'<div style="font-size:13px;color:{C_MUTED};padding-top:3px">'
        f'{" &middot; ".join(meta)}</div>{note}</td>'
        f'<td style="padding:12px 0 12px 12px;border-top:1px solid {C_LINE};'
        f'vertical-align:top;text-align:right;white-space:nowrap">'
        f'<div style="font-size:17px;font-weight:700;color:{C_TEXT}">'
        f'{_kc(it["price"])}</div>{per_l}</td></tr>')


def build_html(sub, its, site_url):
    its = sorted(its, key=lambda x: (x["price_per_l"] is None,
                                     x["price_per_l"] or 0, x["price"]))
    shown = its[:MAX_ITEMS_PER_MAIL]
    more = (f'<p style="margin:16px 0 0;font-size:14px;color:{C_MUTED}">'
            f'… a dalších {len(its) - len(shown)} akcí na webu.</p>'
            if len(its) > len(shown) else "")
    site = html.escape(site_url)
    unsub = html.escape(f"{site_url}/api/unsubscribe.php?t={sub['token']}")
    link = f"color:{C_ACCENT};text-decoration:none"
    return f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Pivo v akci</title></head>
<body style="margin:0;padding:0;background:{C_BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{C_BG}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;font-family:{FONT};color:{C_TEXT}">
<tr><td style="padding:0 4px 16px;font-size:20px;font-weight:700">🍺 Pivo v akci</td></tr>
<tr><td style="background:{C_CARD};border:1px solid {C_LINE};border-radius:14px;padding:22px 22px 10px">
<p style="margin:0 0 6px;font-size:15px">Dobrý den,</p>
<p style="margin:0 0 14px;font-size:15px">od posledně přibyly tyto akce podle vašeho výběru:</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{"".join(_item_html(it) for it in shown)}
</table>{more}
<p style="margin:18px 0 12px;text-align:center">
<a href="{site}" style="display:inline-block;padding:10px 20px;border-radius:12px;background:{C_SOFT};border:1px solid {C_ACCENT};color:{C_TEXT};font-weight:600;font-size:14px;text-decoration:none">Všechny akce na webu</a></p>
</td></tr>
<tr><td style="padding:16px 8px;font-size:12px;line-height:1.6;color:{C_MUTED};text-align:center">
Upozornění chodí, jen když se objeví něco nového.<br>
<a href="{site}/#upozorneni" style="{link}">Změnit výběr</a> &nbsp;&middot;&nbsp;
<a href="{unsub}" style="{link}">Odhlásit odběr</a>
</td></tr>
</table></td></tr></table></body></html>"""


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
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(build_mail(sub, matched, site_url),
                                "plain", "utf-8"))
            msg.attach(MIMEText(build_html(sub, matched, site_url),
                                "html", "utf-8"))
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
