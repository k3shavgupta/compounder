#!/usr/bin/env python3
"""Fetch Nifty 500 constituents into nifty500.txt.

NSE publishes the index membership as a CSV. Financials, and anything whose
returns are set by a regulator rather than a business, are dropped here for the
same reason as in the US universe: ROIC is meaningless when debt is the raw
material.

The Indian exclusion list is longer than the US one. Banks and NBFCs alone are
around a third of the index, and the market has more holding companies whose
consolidated numbers describe nothing you can act on.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import requests

SOURCE = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
OUT = Path(__file__).resolve().parent / "nifty500.txt"

# NSE serves nothing to a client that does not look like a browser.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "text/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

EXCLUDED_INDUSTRIES = {
    "Financial Services",
    "Realty",
    "Power",
    "Oil Gas & Consumable Fuels",
}

# Holding companies: consolidated figures describe a portfolio, not an operating
# business, so every ratio in the gate is meaningless for them.
EXCLUDED_SYMBOLS = {
    "BAJAJHLDNG", "TATAINVEST", "JSWHL", "MOTILALOFS",
    "PILANIINVS", "KAMAHOLD", "SUMMITSEC",
}


def main():
    try:
        resp = requests.get(SOURCE, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))
    except Exception as exc:
        sys.exit("could not fetch NSE list ({}). "
                 "Download ind_nifty500list.csv by hand and rerun.".format(exc))

    if not rows:
        sys.exit("no rows returned")

    kept, dropped = [], 0
    for r in rows:
        sym = (r.get("Symbol") or "").strip()
        ind = (r.get("Industry") or "").strip()
        if not sym:
            continue
        if ind in EXCLUDED_INDUSTRIES or sym in EXCLUDED_SYMBOLS:
            dropped += 1
            continue
        kept.append((sym, ind))

    kept.sort()
    lines = [
        "# Nifty 500, financials / realty / power / fuels and holding companies removed.",
        "# {} kept, {} dropped. Regenerate with fetch_nifty500.py".format(len(kept), dropped),
        "# Tickers carry the .NS suffix for Yahoo; strip it for Screener.in.",
        "",
    ]
    lines += ["{:<16}# {}".format(s + ".NS", i) for s, i in kept]
    OUT.write_text("\n".join(lines) + "\n")
    print("kept {}, dropped {} -> {}".format(len(kept), dropped, OUT.name))


if __name__ == "__main__":
    main()
