#!/usr/bin/env python3
"""M2: run the full eight-rule gate and snapshot every run to SQLite.

    python run_ingest.py                 # the whole universe
    python run_ingest.py --detail SBUX   # year-by-year for one company
    python run_ingest.py --no-store      # skip the SQLite write
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table

from gate import roic, rules
from ingest.edgar import Edgar
from report import store

ROOT = Path(__file__).resolve().parent
console = Console()


def load_universe(path):
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def pct(x, digits=0):
    if x is None:
        return "-"
    if x == float("inf"):
        return "∞"
    return "{:.{d}%}".format(x, d=digits)


def num(x, digits=1):
    return "-" if x is None else ("∞" if x == float("inf") else "{:.{d}f}".format(x, d=digits))


def analyse(edgar, ticker):
    cik = edgar.cik_for(ticker)
    if not cik:
        return {"ticker": ticker, "cik": ticker, "status": "no CIK"}
    profile = edgar.profile(cik)
    if not profile:
        return {"ticker": ticker, "cik": cik, "status": "no filings"}

    row = {
        "ticker": ticker,
        "cik": cik,
        "name": profile["name"][:22],
        "sic": profile["sic"],
        "industry": profile["industry"],
    }
    if profile["excluded"]:
        row["status"] = "EXCLUDED"
        return row

    rows = roic.series(edgar.facts(cik) or {})
    metrics = rules.evaluate(rows)
    if not metrics:
        row["status"] = "short history"
        row["rows"] = rows
        return row

    row["rows"] = rows
    row["metrics"] = metrics
    row["status"] = "PASS" if metrics["passed"] else "FAIL"
    return row


def detail(result):
    console.print("\n[bold]{}[/] — {}\n".format(result["ticker"], result.get("name", "")))
    t = Table(box=None, pad_edge=False)
    for col in ("FY", "EBIT $m", "NOPAT $m", "Inv cap $m", "ROIC", "FCF $m", "Net inc $m", "Shares m"):
        t.add_column(col, justify="right")
    for r in result.get("rows", []):
        t.add_row(
            str(r["year"]),
            "{:,.0f}".format(r["ebit"] / 1e6),
            "{:,.0f}".format(r["nopat"] / 1e6),
            "{:,.0f}".format(r["ic"] / 1e6),
            "[bold]{:.1%}[/]".format(r["roic"]),
            "-" if r["fcf"] is None else "{:,.0f}".format(r["fcf"] / 1e6),
            "-" if r["net_income"] is None else "{:,.0f}".format(r["net_income"] / 1e6),
            "-" if r["shares"] is None else "{:,.0f}".format(r["shares"] / 1e6),
        )
    console.print(t)

    m = result.get("metrics")
    if not m:
        return
    labels = {
        1: "median ROIC >= 15%", 2: "ROIC >= 12% in 8/10y", 3: "incremental ROIC >= 12%",
        4: "FCF positive 8/10y", 5: "FCF/net income >= 0.8", 6: "net debt/EBITDA <= 2",
        7: "interest cover >= 5x", 8: "share count +<10% 5y",
    }
    console.print()
    for n, label in labels.items():
        v = m["rules"][n]
        mark = "[green]pass[/]" if v is True else ("[red]FAIL[/]" if v is False else "[dim]n/a[/]")
        console.print("  {}  rule {} — {}".format(mark, n, label))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=str(ROOT / "universe" / "m1_tickers.txt"))
    ap.add_argument("--detail", help="year-by-year for one ticker")
    ap.add_argument("--no-store", action="store_true", help="skip the SQLite snapshot")
    args = ap.parse_args()

    edgar = Edgar()
    tickers = [args.detail] if args.detail else load_universe(args.universe)

    results = []
    with console.status("[dim]fetching...") as status:
        for t in tickers:
            status.update("[dim]{}...".format(t))
            try:
                results.append(analyse(edgar, t))
            except Exception as exc:  # one bad filer must not stop the run
                results.append({"ticker": t, "cik": t, "status": "ERROR: {}".format(exc)[:38]})

    if args.detail:
        detail(results[0])
        return

    # Deliberately narrow: the per-rule detail lives behind --detail. A summary
    # table that wraps is worse than one that omits.
    table = Table(title="M2 — eight-rule gate", title_style="bold", box=None, pad_edge=False)
    for col, just in [("Ticker", "left"), ("ROIC", "right"), ("Durable", "right"),
                      ("Incr", "right"), ("FCF/NI", "right"), ("ND/EB", "right"),
                      ("Cover", "right"), ("Dilut", "right"),
                      ("Failed rules", "left"), ("", "left")]:
        table.add_column(col, justify=just)

    colour = {"PASS": "green", "FAIL": "red", "EXCLUDED": "yellow"}
    for r in results:
        m = r.get("metrics") or {}
        style = colour.get(r["status"], "dim")
        table.add_row(
            r["ticker"],
            pct(m.get("median_roic")),
            "{}/10".format(m["years_above_12"]) if m else "-",
            m.get("incremental_note") or pct(m.get("incremental_roic")),
            num(m.get("fcf_conversion"), 2),
            num(m.get("net_debt_ebitda")),
            num(m.get("interest_coverage")),
            pct(m.get("share_growth_5y")),
            ",".join(str(x) for x in m.get("failed", [])) or "",
            "[{}]{}[/]".format(style, r["status"]),
        )
    console.print()
    console.print(table)

    passed = [r for r in results if r["status"] == "PASS"]
    console.print("\n[dim]{} of {} passed all eight rules[/]".format(len(passed), len(results)))

    if not args.no_store:
        con = store.connect()
        run_id = store.start_run(con, Path(args.universe).name)
        for r in results:
            if r.get("cik"):
                store.save(con, run_id, r)
        store.finish_run(con, run_id, len(results), len(passed))
        con.close()
        console.print("[dim]run {} saved to data/compounder.db[/]".format(run_id))


if __name__ == "__main__":
    main()
