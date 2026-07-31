"""Telegram digest.

Only sends when something is actually on sale. A weekly message that always
arrives trains you to ignore it, and a tool that finds a candidate every single
week is not screening — it is manufacturing.

Silence is therefore the normal output, which is exactly why the caller must
still ping a heartbeat on success: otherwise "nothing qualified" and "the job
died three weeks ago" look identical from the outside.
"""
from __future__ import annotations

import os

import requests

API = "https://api.telegram.org/bot{token}/sendMessage"

# How far above the 200-week line still counts as worth mentioning. Exactly at
# the line is the signal; a little above is worth watching.
WATCH_BAND = 0.05


def build(rows):
    """rows: list of dicts with ticker, distance, price, ma200w, median_roic.
    Returns the message text, or None when there is nothing worth saying."""
    below = [r for r in rows if r["distance"] <= 0]
    near = [r for r in rows if 0 < r["distance"] <= WATCH_BAND]
    if not below and not near:
        return None

    lines = ["*Compounder* — {} passed the gate".format(len(rows))]

    if below:
        lines.append("")
        lines.append("*Below the 200-week line*")
        for r in below:
            lines.append(
                "`{:<6}` {:>6.0%}   ${:,.0f} vs ${:,.0f}   ROIC {:.0%}".format(
                    r["ticker"], r["distance"], r["price"], r["ma200w"], r["median_roic"]
                )
            )

    if near:
        lines.append("")
        lines.append("*Within 5% above*")
        for r in near:
            lines.append(
                "`{:<6}` {:>6.0%}   ${:,.0f} vs ${:,.0f}".format(
                    r["ticker"], r["distance"], r["price"], r["ma200w"]
                )
            )

    lines.append("")
    lines.append("_Passed the gate is not a recommendation. Read the filing._")
    return "\n".join(lines)


def send(text, token=None, chat_id=None):
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    resp = requests.post(
        API.format(token=token),
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def heartbeat(url=None):
    """Ping the dead-man's switch. Called on success whether or not a message
    was sent, so a silent week is distinguishable from a dead job."""
    url = url or os.getenv("HEARTBEAT_URL")
    if not url:
        return False
    try:
        requests.get(url, timeout=10)
        return True
    except requests.RequestException:
        return False
