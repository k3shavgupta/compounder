"""The eight gate rules.

Every rule is binary. Fail one, you're out — there is deliberately no composite
score, because a weighted score lets a company with a dangerous balance sheet
through on the strength of high returns. Quality is a gate; price is a ranking,
and the two never mix.

A rule returns None, not False, when the metric genuinely does not apply to the
company. None never fails the gate — see rule 3, where a business that returns
capital instead of deploying it must not be failed by a reinvestment test.
"""
from __future__ import annotations

from statistics import median

WINDOW = 10
INCREMENTAL_WINDOW = 5

# Thresholds. These are calibrated guesses until the full S&P 500 has been run
# once - if a threshold admits 3 names or 200, it is wrong, not the market.
MEDIAN_ROIC = 0.15
DURABLE_ROIC = 0.12
DURABLE_YEARS = 8
INCREMENTAL_ROIC = 0.12
FCF_POSITIVE_YEARS = 8
FCF_CONVERSION = 0.80
NET_DEBT_EBITDA = 2.0
INTEREST_COVERAGE = 5.0
MAX_SHARE_GROWTH = 0.10

# Incremental ROIC is meaningless unless the capital base actually moved.
MIN_CAPITAL_MOVE = 0.20


def _last(rows, key, n=1):
    """Most recent n non-null values of `key`, oldest first."""
    vals = [r[key] for r in rows if r.get(key) is not None]
    return vals[-n:] if vals else []


def evaluate(rows):
    """Run all eight rules. Returns metrics + per-rule verdicts, or None if
    there isn't enough history to judge."""
    recent = rows[-WINDOW:]
    if len(recent) < 5:
        return None

    m = {"years": len(recent)}

    # --- Rules 1-3: returns on capital -------------------------------------
    roics = [r["roic"] for r in recent]
    m["median_roic"] = median(roics)
    m["years_above_12"] = sum(1 for r in roics if r >= DURABLE_ROIC)

    inc, inc_note = None, None
    if len(rows) >= INCREMENTAL_WINDOW + 1:
        a, b = rows[-(INCREMENTAL_WINDOW + 1)], rows[-1]
        d_ic = b["ic"] - a["ic"]
        if d_ic < MIN_CAPITAL_MOVE * a["ic"]:
            inc_note = "n/m"
        else:
            inc = (b["nopat"] - a["nopat"]) / d_ic
    m["incremental_roic"] = inc
    m["incremental_note"] = inc_note

    # --- Rules 4-5: is the profit real cash? --------------------------------
    fcf = [r["fcf"] for r in recent if r["fcf"] is not None]
    m["fcf_years"] = len(fcf)
    m["fcf_positive_years"] = sum(1 for f in fcf if f > 0)

    # Cumulative, not year-by-year: one bad working-capital year is noise,
    # a decade of profits that never became cash is a fraud signal.
    ni = [r["net_income"] for r in recent if r["net_income"] is not None]
    total_ni = sum(ni)
    m["fcf_conversion"] = (sum(fcf) / total_ni) if fcf and total_ni > 0 else None

    # --- Rules 6-7: balance sheet -------------------------------------------
    # Point-in-time, so the latest year is what matters. Debt risk is about now.
    nd = _last(recent, "net_debt")
    eb = _last(recent, "ebitda")
    m["net_debt_ebitda"] = (nd[0] / eb[0]) if nd and eb and eb[0] > 0 else None

    ebit = _last(recent, "ebit")
    interest = _last(recent, "interest")
    if not ebit:
        m["interest_coverage"] = None
    elif not interest or abs(interest[0]) < 1:
        # No meaningful interest expense means nothing to cover. Treat as
        # comfortably covered rather than as missing data.
        m["interest_coverage"] = float("inf")
    else:
        m["interest_coverage"] = ebit[0] / abs(interest[0])

    # --- Rule 8: dilution ----------------------------------------------------
    sh = [r["shares"] for r in recent if r["shares"] is not None]
    m["share_growth_5y"] = (
        (sh[-1] / sh[-(INCREMENTAL_WINDOW + 1)] - 1)
        if len(sh) >= INCREMENTAL_WINDOW + 1
        else None
    )

    m["rules"] = {
        1: m["median_roic"] >= MEDIAN_ROIC,
        2: m["years_above_12"] >= DURABLE_YEARS and len(recent) >= WINDOW,
        3: None if inc is None else inc >= INCREMENTAL_ROIC,
        4: None if m["fcf_years"] < WINDOW else m["fcf_positive_years"] >= FCF_POSITIVE_YEARS,
        5: None if m["fcf_conversion"] is None else m["fcf_conversion"] >= FCF_CONVERSION,
        6: None if m["net_debt_ebitda"] is None else m["net_debt_ebitda"] <= NET_DEBT_EBITDA,
        7: None if m["interest_coverage"] is None else m["interest_coverage"] >= INTEREST_COVERAGE,
        8: None if m["share_growth_5y"] is None else m["share_growth_5y"] < MAX_SHARE_GROWTH,
    }
    # None is "doesn't apply", never a failure. Only an explicit False rejects.
    m["failed"] = sorted(k for k, v in m["rules"].items() if v is False)
    m["passed"] = not m["failed"]
    return m
