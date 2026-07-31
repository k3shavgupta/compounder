"""SQLite snapshot of every run.

The point is not caching - it's that a score you never recorded can never be
tested. Storing the inputs alongside the verdict means that when a threshold
turns out to be wrong, you can re-derive history instead of re-fetching it.

Raw inputs go in `fundamental`; ratios are computed at query time, so fixing a
formula is a code change rather than a decade of re-downloads.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "compounder.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
    id           INTEGER PRIMARY KEY,
    run_at       TEXT NOT NULL,
    universe     TEXT NOT NULL,
    n_evaluated  INTEGER,
    n_passed     INTEGER
);
CREATE TABLE IF NOT EXISTS company (
    cik      TEXT PRIMARY KEY,
    ticker   TEXT,
    name     TEXT,
    sic      INTEGER,
    industry TEXT
);
CREATE TABLE IF NOT EXISTS fundamental (
    cik        TEXT NOT NULL,
    fy_end     TEXT NOT NULL,
    ebit       REAL, nopat     REAL, invested_capital REAL,
    fcf        REAL, net_income REAL, ebitda REAL,
    net_debt   REAL, interest  REAL, shares REAL,
    PRIMARY KEY (cik, fy_end)
);
CREATE TABLE IF NOT EXISTS score (
    run_id             INTEGER NOT NULL,
    cik                TEXT NOT NULL,
    status             TEXT,
    median_roic        REAL,
    years_above_12     INTEGER,
    incremental_roic   REAL,
    fcf_positive_years INTEGER,
    fcf_conversion     REAL,
    net_debt_ebitda    REAL,
    interest_coverage  REAL,
    share_growth_5y    REAL,
    failed_rules       TEXT,
    PRIMARY KEY (run_id, cik)
);
"""


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def start_run(con, universe):
    cur = con.execute(
        "INSERT INTO run (run_at, universe, n_evaluated, n_passed) VALUES (?,?,0,0)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), universe),
    )
    con.commit()
    return cur.lastrowid


def save(con, run_id, result):
    """Persist one company: identity, per-year fundamentals, and this run's score."""
    cik = result["cik"]
    con.execute(
        "INSERT OR REPLACE INTO company (cik,ticker,name,sic,industry) VALUES (?,?,?,?,?)",
        (cik, result["ticker"], result.get("name"), result.get("sic"),
         result.get("industry")),
    )

    for r in result.get("rows", []):
        con.execute(
            """INSERT OR REPLACE INTO fundamental
               (cik,fy_end,ebit,nopat,invested_capital,fcf,net_income,ebitda,
                net_debt,interest,shares)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (cik, r["end"], r["ebit"], r["nopat"], r["ic"], r["fcf"],
             r["net_income"], r["ebitda"], r["net_debt"], r["interest"], r["shares"]),
        )

    m = result.get("metrics")
    if m:
        cov = m["interest_coverage"]
        con.execute(
            """INSERT OR REPLACE INTO score
               (run_id,cik,status,median_roic,years_above_12,incremental_roic,
                fcf_positive_years,fcf_conversion,net_debt_ebitda,
                interest_coverage,share_growth_5y,failed_rules)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, cik, result["status"], m["median_roic"], m["years_above_12"],
             m["incremental_roic"], m["fcf_positive_years"], m["fcf_conversion"],
             m["net_debt_ebitda"],
             # SQLite has no infinity; store "no debt to cover" as NULL.
             None if cov == float("inf") else cov,
             m["share_growth_5y"], ",".join(str(r) for r in m["failed"])),
        )
    con.commit()


def finish_run(con, run_id, n_evaluated, n_passed):
    con.execute(
        "UPDATE run SET n_evaluated=?, n_passed=? WHERE id=?",
        (n_evaluated, n_passed, run_id),
    )
    con.commit()
