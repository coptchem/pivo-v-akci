#!/usr/bin/env python3
"""Vygeneruje api/config.php pro deploy z proměnných prostředí.

Použití: python scraper/write_php_config.py publish/api/config.php
Zapisuje jen nastavené proměnné EXPORT_TOKEN, SITE_URL, MAIL_FROM.
"""

import os
import sys


def php_str(value):
    """PHP řetězec v jednoduchých uvozovkách (escapuje \\ a ')."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def main(path):
    lines = ["<?php"]
    for name in ("EXPORT_TOKEN", "SITE_URL", "MAIL_FROM"):
        value = os.environ.get(name, "")
        if value:
            lines.append(f"const {name} = {php_str(value)};")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
