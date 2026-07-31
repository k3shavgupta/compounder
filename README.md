# Compounder

A quality screener for US and Indian equities that reads the filings, refuses
claims it cannot prove, and knows how often it is wrong.

## Where this came from

On 31 July 2026, [@NoLimitGains posted a thread](https://x.com/NoLimitGains/status/2083185082513129724)
laying out a checklist for judging whether a business is worth owning: durable
high returns on capital, incremental ROIC, free cash flow as the only real
earnings, a clean balance sheet, honest capital allocation, and a sensible price
against normalised earnings.

The first filter in that thread, which the author attributes to Charlie Munger:

> Start with the 200 week moving average. Munger said if that was the only
> filter you ever used, you would beat the market. A good business dipping under
> that line is usually fear or a macro scare. Above it, you are often paying for
> perfection.

*(Attribution as given in the thread — I have not independently sourced the
quote to Munger.)*

The thread ends by saying most people will agree with all of it and change
nothing. This repository is the attempt to not do that: turn the checklist into
something that runs on a schedule and can be checked.

**This is a research tool, not a signal generator.** It produces a shortlist and
a cited memo. It does not tell you what to buy, and passing the gate is not a
recommendation.

## How it works

```
UNIVERSE (~365)  →  GATE (pass/fail)  →  TRIGGER (price)  →  READER (filings)
```

**Gate on quality, rank on price, and never mix the two.** A single composite
score lets a company with a dangerous balance sheet through on the strength of
high returns. Quality is a gate; cheapness only orders the survivors.

The 200-week average is the *trigger*, not the screen. Screening on price first
gets you a feed full of cheap garbage — plenty of businesses are below their
200-week line because they deserve to be.

### The eight rules

| # | Rule | Catches |
|---|------|---------|
| 1 | Median 10yr ROIC ≥ 15% | Mediocre businesses |
| 2 | ROIC ≥ 12% in 8 of 10 years | One lucky year inflating an average |
| 3 | 5yr incremental ROIC ≥ 12% | A dead reinvestment engine behind a good average |
| 4 | FCF positive in 8 of 10 years | Cash-burning "profitable" companies |
| 5 | Cumulative 10yr FCF / net income ≥ 0.8 | Profits that never became cash |
| 6 | Net debt / EBITDA ≤ 2.0 | Fragile balance sheets |
| 7 | Interest coverage ≥ 5× | The same, from the income side |
| 8 | Share count up < 10% over 5yr | Serial diluters |

Rules 2 and 5 do the heavy lifting. Rule 2 tests durability rather than an
average — a company earning 40% once and 8% nine times has a fine mean and is a
bad business. Rule 5 is the best cheap fraud detector on the list: sustained
profits that never turn into cash is the most common shape of an accounting
blow-up.

Rules 6 and 7 are the ones that are never traded away for a better number
elsewhere. Munger, on why:

> There are three ways to go broke: liquor, ladies, and leverage.

He was only half joking about which of the three actually gets people. A great
business with a fragile balance sheet is still a business that can be forced to
sell at the bottom — which is why leverage is a gate here and not a weighting.

### Excluded before scoring

Banks, NBFCs and insurers (ROIC is meaningless when debt is the raw material),
REITs, utilities and pure commodity producers, anything listed under 10 years,
and — in India — holding companies, where consolidated figures describe a
portfolio rather than a business.

## What it found

Run over the S&P 500 (365 names after sector exclusions), **53 pass**. The
survivors are boring and obvious — Microsoft, Mastercard, Costco, Cintas,
Moody's, Copart. That is the screen working, not failing: those companies are
famous *because* they pass this kind of test.

The calibration was checked rather than assumed:

- **The median incremental ROIC across the index is 12.3%** — roughly the cost
  of capital plus a thin margin. The thread's claim that "most companies earn
  their cost of capital and nothing more" is empirically true on this data.
- **Rule 3's threshold is not knife-edge.** The 16 companies failing on it alone
  spread from 10.6% down to −72.8%, and only 15 of 211 sit anywhere in the
  10–14% band.
- **Rule 4 decides nothing** on this universe — it fires on 23 companies and
  zero of them fail it alone. Kept as cheap insurance, but it is not
  load-bearing.

## The reader

For the questions arithmetic cannot answer — customer concentration, debt
maturity schedules, competition, risk factors — a local model reads the 10-K.

Two stages. An embedding model ranks every chunk of the filing and the LLM sees
only the top handful. That split is what makes an 8 GB consumer GPU viable: no
such card can hold a 150k-token filing in KV cache, and none needs to.

**Every quote is checked against the filing in code.** If the text is not
literally there, the claim is discarded. A model that paraphrases loses the
claim; a model that invents one loses it too.

### Measured, not assumed

`qwen2.5:7b` on an RTX 3060 Ti, scored against 20 hand-labelled findings across
5 filings:

| | |
|---|---|
| Precision | **65%** |
| Citation failures | 35% (claim true, quote does not support it) |
| **Factual errors** | **0%** |
| Misses | 22% of disclosed facts not surfaced |

Per question, the split is stark: **100% on customer concentration and debt
maturity, 20% on risk factors.** Specific factual lookups are reliable; diffuse
qualitative synthesis is not.

Two findings worth stating plainly:

**Mechanical verification proves a quote is real, not that it is evidence.** The
model cited the section heading *"Risks Related to Our Ability to Grow Our
Business"* — genuinely in the filing, supporting nothing. That gap is why the
eval harness exists.

**Retrieval failure masquerades as citation failure.** Fixing retrieval alone
improved citation accuracy 9 points with no prompt change. When the supporting
sentence is not in the excerpts, the model does not refuse — it quotes the
nearest available thing.

Of four attempted improvements, three were measured and rejected. One of the
rejects visibly fixed one filing while silently breaking two others, and only a
sweep across the whole fixture set caught it.

## India

India runs a **deliberately different, shallower gate**, and the two are never
ranked together.

Free sources serve four years of Indian fundamentals. Rules 2, 3 and 8 need ten,
six and six — and those do most of the filtering. Rather than ship a weaker gate
under the same name, the durability half is a quarterly manual screen (the query
is in `universe/india_quality.txt`); the balance-sheet rules, prices and the
200-week trigger are automated. Every India output says so.

Promoter holding and pledged-share percentage are used as extra filters. Both
are mandated quarterly disclosures with no US equivalent, and pledged promoter
stock is one of the cheapest distress signals available anywhere.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # set EDGAR_USER_AGENT to a real contact address
```

```bash
python run_ingest.py --universe universe/sp500.txt   # the gate
python run_weekly.py                                 # 200-week trigger
python run_memo.py ADBE                              # read a filing
python run_eval.py build|label|score                 # measure the reader
python run_india.py                                  # the India half
```

The reader needs [Ollama](https://ollama.com) with `qwen2.5:7b` and
`nomic-embed-text`. Set `OLLAMA_HOST` if it runs on a different machine.

## Design rules

1. **No claim without a citation.** The memo quotes the filing or says nothing.
2. **Store every run.** Scores, inputs, model version — a score never recorded
   can never be tested.
3. **Silence is a valid output.** "No candidates this week" is the correct
   answer most weeks. A tool that always finds something is lying.

## Data

| What | Source | Cost |
|------|--------|------|
| US fundamentals | SEC EDGAR `companyfacts` | Free, no key |
| Prices | Yahoo via yfinance | Free |
| India durability | Screener.in, manual quarterly query | Free tier |
| Filing text | SEC EDGAR | Free |

EDGAR requires a real contact email in the User-Agent and rate-limits to 10
req/sec. Nothing here scrapes Screener.in — the India list is a dated snapshot
you refresh by running the query yourself.

## Licence

MIT. This is a personal research tool published in case the approach is useful.
Nothing here is investment advice.
