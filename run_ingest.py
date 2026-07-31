#!/usr/bin/env python3
"""M1: pull EDGAR fundamentals and print a 10-year ROIC table.

    python run_ingest.py                 # the whole M1 test universe
    python run_ingest.py --detail SBUX   # year-by-year for one company
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table

from gate import roic
from ingest.edgar import Edgar

ROOT = Path(__file__).resolve().parent
console = Console()


def load_universe(path):
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def pct(x):
    return "-" if x is None else "{:.0%}".format(x)


def analyse(edgar, ticker):
    cik = edgar.cik_for(ticker)
    if not cik:
        return {"ticker": ticker, "status": "no CIK"}
    profile = edgar.profile(cik)
    if not profile:
        return {"ticker": ticker, "status": "no filings"}

    row = {"ticker": ticker, "name": profile["name"][:24], "industry": profile["industry"]}
    if profile["excluded"]:
        row["status"] = "EXCLUDED"
        return row

    rows = roic.series(edgar.facts(cik) or {})
    stats = roic.summary(rows)
    if not stats:
        row["status"] = "insufficient history"
        return row

    row.update(stats)
    # rule3 is None when incremental ROIC isn't meaningful - that must not fail
    # the company, only a genuine sub-12% reading does.
    passed = stats["rule1"] and stats["rule2"] and stats["rule3"] is not False
    row["status"] = "PASS" if passed else "FAIL"
    row["rows"] = rows
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=str(ROOT / "universe" / "m1_tickers.txt"))
    ap.add_argument("--detail", help="show the year-by-year series for one ticker")
    args = ap.parse_args()

    edgar = Edgar()
    tickers = [args.detail] if args.detail else load_universe(args.universe)

    results = []
    with console.status("[dim]fetching EDGAR...") as status:
        for t in tickers:
            status.update("[dim]fetching {}...".format(t))
            try:
                results.append(analyse(edgar, t))
            except Exception as exc:  # keep going; one bad filer shouldn't stop the run
                results.append({"ticker": t, "status": "ERROR: {}".format(exc)[:40]})

    if args.detail:
        r = results[0]
        console.print("\n[bold]{}[/] - {}\n".format(r["ticker"], r.get("name", "")))
        t = Table(box=None, pad_edge=False)
        for col in ("FY", "EBIT $m", "NOPAT $m", "Inv capital $m", "ROIC"):
            t.add_column(col, justify="right")
        for row in r.get("rows", []):
            t.add_row(
                str(row["year"]),
                "{:,.0f}".format(row["ebit"] / 1e6),
                "{:,.0f}".format(row["nopat"] / 1e6),
                "{:,.0f}".format(row["ic"] / 1e6),
                "[bold]{:.1%}[/]".format(row["roic"]),
            )
        console.print(t)
        return

    table = Table(title="M1 - 10 year ROIC", title_style="bold")
    table.add_column("Ticker")
    table.add_column("Company")
    table.add_column("Yrs", justify="right")
    table.add_column("Median ROIC", justify="right")
    table.add_column(">=12%", justify="right")
    table.add_column("Incr ROIC", justify="right")
    table.add_column("Result")

    colour = {"PASS": "green", "FAIL": "red", "EXCLUDED": "yellow"}
    for r in results:
        style = colour.get(r["status"], "dim")
        table.add_row(
            r["ticker"],
            r.get("name", ""),
            str(r.get("years", "")),
            pct(r.get("median_roic")),
            "{}/10".format(r["years_above_12"]) if "years_above_12" in r else "",
            r.get("incremental_note") or pct(r.get("incremental_roic")),
            "[{}]{}[/]".format(style, r["status"]),
        )
    console.print()
    console.print(table)

    passed = sum(1 for r in results if r["status"] == "PASS")
    console.print("\n[dim]{} of {} passed rules 1-3[/]".format(passed, len(results)))


if __name__ == "__main__":
    main()
