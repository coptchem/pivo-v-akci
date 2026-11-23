# -*- coding: utf-8 -*-
"""Rozpoznání značky piva z názvu produktu.

Stejný seznam jako BRAND_PATTERNS v index.html – při změně upravit obojí.
Používá se pro anotaci položek v pivo.json (pole "brand") a pro párování
odběratelských preferencí v send_digest.py.
"""

# podřetězec v názvu (malými písmeny) -> kanonická značka; pořadí rozhoduje
BRAND_PATTERNS = [
    ("gambrinus", "Gambrinus"),
    ("pilsner", "Pilsner Urquell"),
    ("kozel", "Kozel"),
    ("radegast", "Radegast"),
    ("budvar", "Budvar"), ("budweiser", "Budvar"),
    ("pardál", "Pardál"), ("pardal", "Pardál"),
    ("staropramen", "Staropramen"),
    ("braník", "Braník"), ("branik", "Braník"),
    ("krušovice", "Krušovice"), ("krusovice", "Krušovice"),
    ("zlatopramen", "Zlatopramen"),
    ("březňák", "Březňák"), ("breznak", "Březňák"),
    ("starobrno", "Starobrno"),
    ("bernard", "Bernard"),
    ("svijan", "Svijany"),
    ("primátor", "Primátor"), ("primator", "Primátor"),
    ("primus", "Primus"),
    ("lobkowicz", "Lobkowicz"),
    ("černá hora", "Černá Hora"), ("cerna hora", "Černá Hora"),
    ("litovel", "Litovel"),
    ("zubr", "Zubr"),
    ("holba", "Holba"),
    ("poutník", "Poutník"), ("poutnik", "Poutník"),
    ("bakalář", "Bakalář"), ("bakalar", "Bakalář"),
    ("rohozec", "Rohozec"),
    ("chotěboř", "Chotěboř"), ("chotebor", "Chotěboř"),
    ("ferdinand", "Ferdinand"),
    ("samson", "Samson"),
    ("regent", "Regent"),
    ("platan", "Platan"),
    ("dudák", "Dudák"), ("dudak", "Dudák"),
    ("klášter", "Klášter"), ("klaster", "Klášter"),
    ("rychtář", "Rychtář"), ("rychtar", "Rychtář"),
    ("birell", "Birell"),
    ("heineken", "Heineken"),
    ("zlatý bažant", "Zlatý Bažant"), ("zlaty bazant", "Zlatý Bažant"),
    ("desperados", "Desperados"),
    ("corona", "Corona"),
    ("stella artois", "Stella Artois"),
    ("hoegaarden", "Hoegaarden"),
    ("leffe", "Leffe"),
    ("paulaner", "Paulaner"),
    ("erdinger", "Erdinger"),
    ("krombacher", "Krombacher"),
    ("warsteiner", "Warsteiner"),
    ("argus", "Argus"),
    ("perlenbacher", "Perlenbacher"),
    ("excelent", "Excelent"),
    ("velvet", "Velvet"),
]


def brand_of(name):
    low = name.lower()
    for pat, canonical in BRAND_PATTERNS:
        if pat in low:
            return canonical
    return None
