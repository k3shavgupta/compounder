#!/usr/bin/env python3
"""M3: the weekly trigger.

Reads the survivors of the most recent gate run, prices them, and reports the
ones trading at or below their 200-week moving average.

    python run_weekly.py            # print only
    python run_weekly.py --send     # print and send the Telegram digest

The gate is quality and runs on filings; this is price and runs on the market.
Keeping them separate is the point: cheapness orders the list, it never gets
anything onto it.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from ingest import prices
from report import digest, store

ROOT = Path(__file__).resolve().parent
console = Console()


def latest_passers(con):
    row = con.execute("SELECT MAX(id) FROM run").fetchone()
    if not row or row[0] is None:
        return None, []
    run_id = row[0]
    rows = con.execute(
        """SELECT c.ticker, s.median_roic
           FROM score s JOIN company c USING(cik)
           WHERE s.run_id = ? AND s.status = 'PASS'
           ORDER BY c.ticker""",
        (run_id,),
    ).fetchall()
    return run_id, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="send the Telegram digest")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    con = store.connect()
    run_id, passers = latest_passers(con)
    con.close()

    if not passers:
        console.print("[red]no completed gate run found — run run_ingest.py first[/]")
        return 1

    console.print("[dim]run {} — {} names passed the gate[/]".format(run_id, len(passers)))
    roic = {t: r for t, r in passers}

    quotes = prices.fetch(roic.keys())
    missing = sorted(set(roic) - set(quotes))

    rows = [
        {"ticker": t, "median_roic": roic[t], **q}
        for t, q in quotes.items()
    ]
    rows.sort(key=lambda r: r["distance"])

    table = Table(title="200-week moving average", title_style="bold",
                  box=None, pad_edge=False)
    for col in ("Ticker", "Price", "200wMA", "vs MA", "ROIC"):
        table.add_column(col, justify="right" if col != "Ticker" else "left")
    for r in rows[:20]:
        colour = "green" if r["distance"] <= 0 else ("yellow" if r["distance"] <= 0.05 else "dim")
        table.add_row(
            r["ticker"],
            "{:,.0f}".format(r["price"]),
            "{:,.0f}".format(r["ma200w"]),
            "[{}]{:+.0%}[/]".format(colour, r["distance"]),
            "{:.0%}".format(r["median_roic"]),
        )
    console.print()
    console.print(table)
    if len(rows) > 20:
        console.print("[dim]…{} more further above the line[/]".format(len(rows) - 20))
    if missing:
        # Never silently drop a name — a company absent from the price feed is
        # a company you are not watching, and you should know which.
        console.print("[dim]no usable price history: {}[/]".format(", ".join(missing)))

    text = digest.build(rows)
    if text is None:
        console.print("\n[bold]Nothing at or near the line. No message sent.[/]")
    else:
        console.print("\n[bold]Digest:[/]\n")
        console.print(text)

    if args.send:
        if text:
            digest.send(text)
            console.print("\n[green]sent[/]")
        digest.heartbeat()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
