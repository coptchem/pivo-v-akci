# -*- coding: utf-8 -*-
"""Scrapery jednotlivých řetězců. Každý modul má funkci scrape(today).

Penny a COOP se berou ze záložního zdroje (Penny web publikuje jen pár
akcí, COOP je roztříštěný na regionální Jednoty bez celostátního zdroje)
- viz scrape.py. Albert má jen obrázkové letáky - řeší modul albert přes
Claude API. Moduly penny a coop (leták Terno ČB) zůstávají k dispozici.
"""

from chains import albert, billa, coop, globus, kaufland, lidl, tesco  # noqa: F401

SCRAPERS = {
    "Kaufland": kaufland.scrape,
    "Lidl": lidl.scrape,
    "Billa": billa.scrape,
    "Tesco": tesco.scrape,
    "Globus": globus.scrape,
    "Albert": albert.scrape,
}
