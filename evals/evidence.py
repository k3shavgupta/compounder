#!/usr/bin/env python3
"""Keyword evidence for the truth set.

Answering "does this filing disclose X" by reading 200 pages is not going to
happen. This greps the cached filings for the phrases such a disclosure would
have to use and prints the hits with context.

Keyword search is deliberately a DIFFERENT mechanism from the embedding
retrieval being evaluated, so a hit here is independent evidence rather than
the system marking its own homework. It is still a prompt for your judgement,
not the judgement itself — grep both over- and under-matches.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import filings  # noqa: E402
from ingest.edgar import Edgar  # noqa: E402

PATTERNS = {
    "customer_concentration": [
        r"10%\s+(?:or more\s+)?of\s+(?:our\s+)?(?:total\s+|net\s+|consolidated\s+)?(?:revenue|sales)",
        r"(?:no|any)\s+(?:single\s+)?customer\s+accounted",
        r"customers?\s+accounted\s+for\s+(?:more than\s+)?\d+",
        r"concentration\s+of\s+credit\s+risk",
    ],
    "debt_maturity": [
        r"maturities\s+of\s+long-term\s+debt",
        r"principal\s+payments?\s+due",
        r"(?:matures|maturity date)\s+(?:on|of)\s+\w+\s+\d",
        r"senior notes due",
    ],
}
WINDOW = 110


def main():
    tickers = sys.argv[1:] or ["ANET", "ADBE", "TTD", "LULU", "COST"]
    edgar = Edgar()
    for tk in tickers:
        cik = edgar.cik_for(tk)
        f = edgar.latest_10k(cik) if cik else None
        if not f:
            print("{}: no 10-K".format(tk))
            continue
        text = filings.to_text(edgar.raw(f["url"], "doc_" + f["accession"].replace("-", "")))
        flat = re.sub(r"\s+", " ", text)
        print("\n=== {} ({}) ===".format(tk, f["date"]))
        for q, pats in PATTERNS.items():
            hits = []
            for p in pats:
                for m in re.finditer(p, flat, re.I):
                    s = max(0, m.start() - WINDOW)
                    hits.append(flat[s:m.end() + WINDOW].strip())
                    if len(hits) >= 3:
                        break
                if len(hits) >= 3:
                    break
            print("\n  {} — {} hit(s)".format(q, len(hits)))
            for h in hits:
                print("     ...{}...".format(h))


if __name__ == "__main__":
    main()
