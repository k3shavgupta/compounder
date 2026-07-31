"""Weekly prices and the 200-week moving average.

Prices must be split- and dividend-adjusted or the moving average is nonsense —
a 4-for-1 split drops the raw series 75% overnight and every name looks like a
generational bargain. yfinance's auto_adjust handles it; do not turn it off.

200 weeks is just under four years, so the request needs a decade of history to
leave room for names with gaps.
"""
from __future__ import annotations

import warnings

import pandas as pd

WINDOW_WEEKS = 200
MIN_WEEKS = 200


def fetch(tickers, period="10y"):
    """{ticker: {price, ma200w, distance}} — distance is negative below the MA.

    Tickers with fewer than 200 weeks of history are omitted rather than given a
    short-window average: a 60-week mean is not a 200-week mean, and quietly
    substituting one would put a young company at the top of the buy list.
    """
    import yfinance as yf

    tickers = list(tickers)
    if not tickers:
        return {}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data = yf.download(
            tickers,
            period=period,
            interval="1wk",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

    if data is None or data.empty:
        return {}

    close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
    if not isinstance(data.columns, pd.MultiIndex):
        close.columns = tickers[:1]

    out = {}
    for t in tickers:
        if t not in close.columns:
            continue
        s = close[t].dropna()
        if len(s) < MIN_WEEKS:
            continue
        ma = s.rolling(WINDOW_WEEKS).mean().iloc[-1]
        price = s.iloc[-1]
        if pd.isna(ma) or pd.isna(price) or ma <= 0:
            continue
        out[t] = {
            "price": float(price),
            "ma200w": float(ma),
            "distance": float(price / ma - 1.0),
            "weeks": len(s),
        }
    return out
