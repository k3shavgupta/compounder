"""Build a per-year ROIC series out of EDGAR companyfacts.

Invested capital is computed from the ASSET side:

    IC = total assets - cash and equivalents - (current liabilities - short-term debt)

The textbook financing definition (debt + equity - cash) blows up on companies
that have bought back enough stock to drive book equity negative - Starbucks,
Home Depot, McDonald's, Apple at times. Those give you a tiny or negative
denominator and a ROIC of 900% or -400%, which is not a signal, it is a bug.
The operating definition asks how much capital is actually deployed in the
business and is immune to capital-structure games.

Everything below prefers the most recently filed value for a period, so
restatements win over originals.
"""
from __future__ import annotations

from datetime import date
from statistics import median

ANNUAL_DAYS = (340, 400)
ANNUAL_FORMS = ("10-K", "10-K/A")

EBIT = ["OperatingIncomeLoss"]
PRETAX = [
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
]
INTEREST = ["InterestExpense", "InterestExpenseNonoperating", "InterestIncomeExpenseNet"]
TAX = ["IncomeTaxExpenseBenefit"]

ASSETS = ["Assets"]
# Two ways filers report the same thing. CASH_PLUS_INVEST is already inclusive,
# so it must never be combined with SHORT_INVEST or the investments come off
# the balance sheet twice.
CASH_PLUS_INVEST = ["CashCashEquivalentsAndShortTermInvestments"]
CASH = ["CashAndCashEquivalentsAtCarryingValue"]
SHORT_INVEST = ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"]
CURRENT_LIAB = ["LiabilitiesCurrent"]
SHORT_DEBT = ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"]


def _rows(facts, tag):
    node = (facts.get("facts", {}).get("us-gaap", {}) or {}).get(tag)
    if not node:
        return []
    for rows in node.get("units", {}).values():
        return rows
    return []


def _series(facts, tags, instant, per_year=False):
    """{period_end_date: value} resolved from a fallback chain of tags.

    per_year=True fills each year from the first tag that covers it. Needed
    where filers switch tags mid-history for the same quantity: Microsoft only
    began using ShortTermInvestments in FY2018, and taking the first tag with
    *any* data left every earlier year with $120bn un-subtracted.

    per_year=False (the default) takes the first tag with any data at all, and
    is right where the tags mean genuinely different things - the pretax income
    variants below differ on equity-method income and on domestic vs total, so
    mixing them across years would silently change definition mid-series.
    """
    out = {}
    for tag in tags:
        found = {}
        for row in _rows(facts, tag):
            if row.get("form") not in ANNUAL_FORMS:
                continue
            if instant:
                if "start" in row:
                    continue
            else:
                if "start" not in row:
                    continue
                span = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                if not ANNUAL_DAYS[0] <= span <= ANNUAL_DAYS[1]:
                    continue
            end, filed = row["end"], row.get("filed", "")
            if end not in found or filed > found[end][1]:
                found[end] = (row["val"], filed)
        for end, (val, _) in found.items():
            out.setdefault(end, val)
        if out and not per_year:
            break
    return out


def _effective_tax_rate(tax, pretax):
    """Clamped to 0-45%. Loss years and one-off settlements produce rates like
    -300% or 1200%, which would swamp NOPAT if passed through."""
    if pretax is None or tax is None or pretax <= 0:
        return 0.25
    rate = tax / pretax
    return min(max(rate, 0.0), 0.45)


def series(facts):
    """List of per-year dicts, oldest first."""
    ebit = _series(facts, EBIT, instant=False)
    pretax = _series(facts, PRETAX, instant=False)
    interest = _series(facts, INTEREST, instant=False)
    tax = _series(facts, TAX, instant=False)

    assets = _series(facts, ASSETS, instant=True)
    combined = _series(facts, CASH_PLUS_INVEST, instant=True)
    cash = _series(facts, CASH, instant=True)
    sti = _series(facts, SHORT_INVEST, instant=True, per_year=True)
    cur_liab = _series(facts, CURRENT_LIAB, instant=True)
    st_debt = _series(facts, SHORT_DEBT, instant=True)

    def cash_like(day):
        """The inclusive tag wins outright where present; otherwise add the parts."""
        if day in combined:
            return combined[day]
        return cash.get(day, 0.0) + sti.get(day, 0.0)

    def invested_capital(day):
        if day not in assets:
            return None
        ic = assets[day] - cash_like(day)
        if day in cur_liab:
            ic -= cur_liab[day] - st_debt.get(day, 0.0)
        return ic if ic > 0 else None

    out = []
    ends = sorted(set(ebit) | set(pretax))
    for i, day in enumerate(ends):
        op = ebit.get(day)
        if op is None and day in pretax:
            # Rebuild EBIT as pretax income + interest expense. Banks aside,
            # this is the standard reconstruction when a filer omits
            # OperatingIncomeLoss.
            op = pretax[day] + (interest.get(day) or 0.0)
        if op is None:
            continue

        nopat = op * (1 - _effective_tax_rate(tax.get(day), pretax.get(day)))
        ic_end = invested_capital(day)
        ic_start = invested_capital(ends[i - 1]) if i else None
        if ic_end is None:
            continue
        # Average capital over the year matches the flow in the numerator.
        # Ending-only inflates ROIC for anyone who grew during the year.
        ic_avg = (ic_end + ic_start) / 2 if ic_start else ic_end

        out.append(
            {
                "year": int(day[:4]),
                "end": day,
                "ebit": op,
                "nopat": nopat,
                "ic": ic_avg,
                "roic": nopat / ic_avg,
            }
        )
    return out


def summary(rows, window=10):
    """Rules 1-3 of the gate."""
    recent = rows[-window:]
    if len(recent) < 5:
        return None

    roics = [r["roic"] for r in recent]
    inc, inc_note = None, None
    if len(rows) >= 6:
        a, b = rows[-6], rows[-1]
        d_ic = b["ic"] - a["ic"]
        # Incremental ROIC is only meaningful if the capital base actually
        # moved. Apple, Visa and Southwest all returned cash rather than
        # reinvesting, so their IC barely budged and the ratio explodes into
        # the hundreds of percent. Home Depot and Starbucks hit the same wall
        # from the other side and score near zero. Neither number says anything
        # about reinvestment quality, so refuse to compute it below a 20%
        # move rather than emit a confident-looking figure.
        if d_ic < 0.20 * a["ic"]:
            inc_note = "n/m"
        else:
            inc = (b["nopat"] - a["nopat"]) / d_ic

    # rule3 is None, not False, when the metric doesn't apply. A business that
    # returns capital instead of deploying it should not fail a reinvestment
    # test - that is a category error, not a red flag.
    rule3 = None if inc is None else inc >= 0.12

    return {
        "years": len(recent),
        "median_roic": median(roics),
        "years_above_12": sum(1 for r in roics if r >= 0.12),
        "incremental_roic": inc,
        "incremental_note": inc_note,
        "rule1": median(roics) >= 0.15,
        "rule2": sum(1 for r in roics if r >= 0.12) >= 8 and len(recent) >= 10,
        "rule3": rule3,
    }
