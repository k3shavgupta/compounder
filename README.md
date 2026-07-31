# Compounder

A quality-screener agent for US and Indian equities.

Maintains a ranked watchlist of businesses that pass a hard quality gate, and
flags them when they trade near or below their 200-week moving average.

**This is a research tool, not a signal generator.** It produces a shortlist and
a cited memo. It does not tell you what to buy.

## The idea

Two stages, deliberately kept separate:

```
UNIVERSE (~700)  ->  GATE (pass/fail)  ->  RANK (order survivors)
```

Gate on quality. Rank on price. Cheapness orders the list; it never gets
anything onto the list.

## The gate

Eight binary rules. Fail one, you're out.

| # | Rule | Catches |
|---|------|---------|
| 1 | Median 10yr ROIC >= 15% | Mediocre businesses |
| 2 | ROIC >= 12% in 8 of 10 years | One lucky year inflating the average |
| 3 | 5yr incremental ROIC >= 12% | Dead reinvestment engine |
| 4 | FCF positive in 8 of 10 years | Cash-burning "profitable" companies |
| 5 | Cumulative 10yr FCF / net income >= 0.8 | Profits that never became cash |
| 6 | Net debt / EBITDA <= 2.0 | Fragile balance sheets |
| 7 | Interest coverage >= 5x | Same, from the income side |
| 8 | Share count up < 10% over 5yr | Serial diluters |

Rules 2 and 5 do the heavy lifting. Rule 2 tests durability instead of average.
Rule 5 is the best fraud detector on the list.

## Universe exclusions

Applied before any scoring:

- Banks, NBFCs, insurers - ROIC is meaningless when debt is the raw material
- REITs / InvITs - same problem
- Utilities, pure commodity producers - regulated or cyclical returns
- Listed under 10 years - can't test durability without history
- India: holding companies - consolidated numbers are nonsense
- India: promoter pledge > 10%
- India: ASM/GSM surveillance list

## Milestones

- [x] **M1** EDGAR -> 10-year ROIC table, 20 tickers, printed to terminal
- [ ] **M2** Full gate + composite rank, SQLite snapshot each run
- [x] **M3** 200-week MA trigger + Telegram digest, scheduled on aviato
- [ ] **M4** LLM reads the 10-K for what arithmetic can't answer, with citations
- [ ] **M5** Eval harness against hand-labelled filings
- [ ] **M6** India via Nifty 500

M1-M3 is a working tool. M4-M5 is the part worth showing someone.

## Non-negotiables

1. **No claim without a citation.** The memo quotes the filing or says "not found".
2. **Store every run.** Scores, inputs, model version. Otherwise the score can
   never be tested.
3. **Silence is a valid output.** "No candidates this week" is the correct answer
   most weeks. A tool that always finds something is lying.

## Setup

```bash
cd ~/compounder
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Data sources

| What | Source | Cost |
|------|--------|------|
| US fundamentals | SEC EDGAR `companyfacts` API | Free, no key |
| US prices | yfinance | Free |
| India fundamentals | screener.in (manual export at first) | Free tier |
| India prices | yfinance `.NS` / Kite | Free |

EDGAR requires a real User-Agent with a contact email and rate-limits to
10 req/sec. Set `EDGAR_USER_AGENT` in `.env`. For the initial backfill use SEC's
bulk `companyfacts.zip` rather than hitting the API 900 times.
