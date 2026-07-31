#!/usr/bin/env python3
"""Fetch S&P 500 constituents into sp500.txt.

Membership isn't in EDGAR — SEC knows who files, not who is in an index. This
pulls the list from a maintained dataset and writes one ticker per line with the
GICS sector as a comment, so exclusions stay auditable by eye.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import requests

SOURCE = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)
OUT = Path(__file__).resolve().parent / "sp500.txt"

# Sectors the gate cannot score, dropped here rather than silently later.
# Financials and real estate: ROIC is meaningless when debt is the raw material.
# Utilities: returns are set by a regulator, not by the business.
EXCLUDED_SECTORS = {"Financials", "Real Estate", "Utilities"}


def main():
    resp = requests.get(SOURCE, timeout=60)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    if not rows:
        sys.exit("no rows returned")

    kept, dropped = [], []
    for r in rows:
        sym = (r.get("Symbol") or "").strip()
        sector = (r.get("GICS Sector") or "").strip()
        if not sym:
            continue
        (dropped if sector in EXCLUDED_SECTORS else kept).append((sym, sector))

    kept.sort()
    lines = [
        "# S&P 500 constituents, financials / real estate / utilities removed.",
        "# {} kept, {} dropped by sector. Regenerate with fetch_sp500.py".format(
            len(kept), len(dropped)
        ),
        "",
    ]
    lines += ["{:<8}# {}".format(sym, sector) for sym, sector in kept]
    OUT.write_text("\n".join(lines) + "\n")
    print("kept {}, dropped {} by sector -> {}".format(len(kept), len(dropped), OUT.name))


if __name__ == "__main__":
    main()
