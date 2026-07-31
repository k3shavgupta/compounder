#!/usr/bin/env python3
"""M6: the India half.

    python run_india.py            # print
    python run_india.py --send     # print and send the Telegram digest
    python run_india.py --check    # shallow balance-sheet check over the universe

India runs a DIFFERENT gate from the US and the two must never share a ranking.
yfinance serves four years of Indian financials; rules 2, 3 and 8 need ten, six
and six. Those are the rules that do most of the filtering, so an automated
four-year gate would carry the same name as the US one and mean far less.

So the work splits by what the data can actually support:

  durability   MANUAL. A quarterly Screener.in query, curated into
               universe/india_quality.txt. Screener has the ten years.
  balance sheet + price   AUTOMATED. Both need only current or price data,
               and the 200-week average works fine — Yahoo serves a decade of
               weekly bars for .NS tickers.

Every output is labelled so a name that cleared the shallow gate is never
mistaken for one that cleared the deep one.
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from gate import rules
from ingest import prices
from report import digest

ROOT = Path(__file__).resolve().parent
QUALITY = ROOT / "universe" / "india_quality.txt"
UNIVERSE = ROOT / "universe" / "nifty500.txt"
console = Console()


def load_list(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def balance_sheet(ticker):
    """Rules 6 and 7 only — the two the four-year window can actually support.

    Returns None when Yahoo has no usable statements, which happens often enough
    for smaller Indian names that it must be reported rather than treated as a
    pass.
    """
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = yf.Ticker(ticker)
        try:
            fin, bs = t.financials, t.balance_sheet
        except Exception:
            return None
    if fin is None or bs is None or fin.empty or bs.empty:
        return None

    def pick(frame, *names):
        for n in names:
            if n in frame.index:
                v = frame.loc[n].dropna()
                if len(v):
                    return float(v.iloc[0])
        return None

    ebit = pick(fin, "EBIT", "Operating Income")
    interest = pick(fin, "Interest Expense")
    debt = pick(bs, "Total Debt")
    cash = pick(bs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    da = pick(fin, "Reconciled Depreciation")

    out = {"ebit": ebit}
    out["interest_coverage"] = (
        float("inf") if not interest or abs(interest) < 1
        else (ebit / abs(interest) if ebit else None))
    ebitda = (ebit + da) if (ebit is not None and da is not None) else ebit
    net_debt = (debt or 0.0) - (cash or 0.0)
    out["net_debt_ebitda"] = (net_debt / ebitda) if ebitda and ebitda > 0 else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="run the shallow balance-sheet check over the quality list")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    quality = load_list(QUALITY)
    if not quality:
        console.print("[yellow]universe/india_quality.txt is empty.[/]\n")
        console.print("The durability rules cannot be computed from Yahoo's 4 years of")
        console.print("Indian financials, so that half of the gate is a quarterly")
        console.print("Screener.in query. The query is in the file — paste it at")
        console.print("[bold]screener.in/screen/new/[/] and copy the symbols back in.\n")
        console.print("[dim]{} Nifty 500 names are ready in universe/nifty500.txt[/]".format(
            len(load_list(UNIVERSE))))
        return 1

    console.print("[dim]{} names on the India quality list[/]".format(len(quality)))

    if args.check:
        t = Table(title="shallow check — rules 6 & 7 only", title_style="bold",
                  box=None, pad_edge=False)
        for c in ("Ticker", "ND/EBITDA", "IntCover", "Verdict"):
            t.add_column(c, justify="left" if c == "Ticker" else "right")
        for tk in quality:
            m = balance_sheet(tk)
            if not m:
                t.add_row(tk, "-", "-", "[dim]no data[/]")
                continue
            nd, cov = m["net_debt_ebitda"], m["interest_coverage"]
            bad = []
            if nd is not None and nd > rules.NET_DEBT_EBITDA:
                bad.append("6")
            if cov is not None and cov < rules.INTEREST_COVERAGE:
                bad.append("7")
            t.add_row(
                tk,
                "-" if nd is None else "{:.1f}".format(nd),
                "∞" if cov == float("inf") else ("-" if cov is None else "{:.1f}".format(cov)),
                "[red]fails {}[/]".format(",".join(bad)) if bad else "[green]ok[/]")
        console.print()
        console.print(t)
        return 0

    quotes = prices.fetch(quality)
    missing = sorted(set(quality) - set(quotes))
    rows = [{"ticker": tk.replace(".NS", ""), "median_roic": 0.0, **q}
            for tk, q in quotes.items()]
    rows.sort(key=lambda r: r["distance"])

    t = Table(title="India — 200-week moving average", title_style="bold",
              box=None, pad_edge=False)
    for c in ("Ticker", "Price", "200wMA", "vs MA"):
        t.add_column(c, justify="left" if c == "Ticker" else "right")
    for r in rows[:25]:
        colour = "green" if r["distance"] <= 0 else ("yellow" if r["distance"] <= 0.05 else "dim")
        t.add_row(r["ticker"], "{:,.0f}".format(r["price"]),
                  "{:,.0f}".format(r["ma200w"]),
                  "[{}]{:+.0%}[/]".format(colour, r["distance"]))
    console.print()
    console.print(t)
    if missing:
        console.print("[dim]no usable price history: {}[/]".format(", ".join(missing)))

    below = [r for r in rows if r["distance"] <= 0]
    if not below:
        console.print("\n[bold]Nothing at or below the line.[/]")
        if args.send:
            digest.heartbeat()
        return 0

    lines = ["*Compounder — India* ({} on the quality list)".format(len(quality)), ""]
    lines.append("*Below the 200-week line*")
    for r in below:
        lines.append("`{:<12}` {:>6.0%}   Rs{:,.0f} vs Rs{:,.0f}".format(
            r["ticker"], r["distance"], r["price"], r["ma200w"]))
    lines += ["", "_Shallow gate: durability rules are the manual Screener list,_",
              "_not computed. Do not rank these against US names._"]
    text = "\n".join(lines)
    console.print("\n[bold]Digest:[/]\n")
    console.print(text)
    if args.send:
        digest.send(text)
        digest.heartbeat()
        console.print("\n[green]sent[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
