#!/usr/bin/env python3
"""M4: read a company's 10-K with a local model and produce a cited memo.

    python run_memo.py ADBE
    python run_memo.py ADBE --model llama3.1:8b

Every claim carries a quote that was checked against the filing. Claims whose
quote could not be found are discarded and counted, not shown — an unverifiable
finding is worse than no finding, because it reads exactly like a real one.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from gate import reader
from ingest import filings
from ingest.edgar import Edgar

ROOT = Path(__file__).resolve().parent
console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--model", default=None, help="override the chat model")
    ap.add_argument("--host", default=None, help="override the Ollama host")
    ap.add_argument("--top-k", type=int, default=reader.TOP_K)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    edgar = Edgar()
    cik = edgar.cik_for(args.ticker)
    if not cik:
        console.print("[red]unknown ticker[/]")
        return 1

    filing = edgar.latest_10k(cik)
    if not filing:
        console.print("[red]no 10-K on file[/]")
        return 1

    console.print("[dim]{} — 10-K filed {}[/]".format(args.ticker, filing["date"]))
    html = edgar.raw(filing["url"], "doc_" + filing["accession"].replace("-", ""))
    text = filings.to_text(html)
    chunks = filings.chunk(text)
    console.print("[dim]{:,} chars -> {} chunks[/]".format(len(text), len(chunks)))

    t0 = time.time()
    with console.status("[dim]embedding...[/]"):
        vecs = reader.embed(chunks, host=args.host)
    console.print("[dim]embedded in {:.0f}s[/]".format(time.time() - t0))

    total_found = total_rejected = 0
    for q in reader.QUESTIONS:
        qvec = reader.embed([q["probe"]], host=args.host)[0]
        idx = reader.rank(qvec, vecs, k=args.top_k, chunks=chunks, terms=q.get("terms") if q.get("hybrid") else None)
        excerpts = [chunks[i] for i in idx]

        with console.status("[dim]{}...[/]".format(q["key"])):
            answer = reader.ask(q["ask"], excerpts, host=args.host, model=args.model)
        findings, rejected = reader.verify(answer, text)
        total_found += len(findings)
        total_rejected += rejected

        console.print("\n[bold]{}[/]".format(q["key"].replace("_", " ").upper()))
        if not findings:
            reason = "nothing verifiable" if rejected else "not disclosed in the excerpts"
            console.print("  [dim]{}{}[/]".format(
                reason, " ({} unverifiable dropped)".format(rejected) if rejected else ""))
            continue
        for f in findings:
            console.print("  [green]•[/] {}".format(f["claim"]))
            console.print('    [dim]"{}"[/]'.format(f["quote"][:200]))
        if rejected:
            console.print("  [yellow]{} claim(s) dropped — quote not in filing[/]".format(rejected))

    console.print("\n[bold]{} verified, {} dropped[/]  [dim]{}[/]".format(
        total_found, total_rejected, filing["url"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
