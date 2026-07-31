"""SEC EDGAR XBRL client.

EDGAR wants a real contact email in the User-Agent and rate-limits to 10 req/sec.
Responses are cached to disk gzipped and reused: filings are immutable once
published, so re-downloading 900 companies every time a formula changes is pure
waste. Delete data/cache/ to force a refresh.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"

TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SIC ranges we refuse to score rather than score badly.
# 6000-6799 is finance, insurance and real estate: ROIC is meaningless when debt
# is the raw material rather than the funding. 4900-4949 is utilities, where
# returns are set by a regulator, not by the business.
EXCLUDED_SIC = ((6000, 6799), (4900, 4949))


class Edgar:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        ua = os.getenv("EDGAR_USER_AGENT", "")
        if "@" not in ua:
            raise SystemExit(
                "EDGAR_USER_AGENT must be 'Your Name you@example.com' in .env - "
                "SEC blocks requests without a contact address."
            )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self.interval = 1.0 / float(os.getenv("EDGAR_RATE_LIMIT", "5"))
        self._last = 0.0
        CACHE.mkdir(parents=True, exist_ok=True)

    def _get(self, url, key, max_age=None):
        path = CACHE / (key + ".json.gz")
        if path.exists() and (max_age is None or time.time() - path.stat().st_mtime < max_age):
            with gzip.open(path, "rt") as fh:
                return json.load(fh)

        wait = self.interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        resp = self.session.get(url, timeout=60)
        self._last = time.time()
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        with gzip.open(path, "wt") as fh:
            json.dump(data, fh)
        return data

    def cik_for(self, ticker):
        """SEC uses dashes where exchanges use dots: BRK.B is filed as BRK-B."""
        data = self._get(TICKER_MAP, "company_tickers", max_age=7 * 86400)
        want = ticker.upper().replace(".", "-")
        for row in data.values():
            if row["ticker"].upper() == want:
                return "{:010d}".format(row["cik_str"])
        return None

    def profile(self, cik):
        data = self._get(SUBMISSIONS.format(cik=cik), "sub_" + cik, max_age=86400)
        if not data:
            return None
        try:
            sic = int(data.get("sic") or 0)
        except ValueError:
            sic = 0
        excluded = any(lo <= sic <= hi for lo, hi in EXCLUDED_SIC)
        return {
            "name": data.get("name", ""),
            "sic": sic,
            "industry": data.get("sicDescription", ""),
            "excluded": excluded,
        }

    def facts(self, cik):
        return self._get(COMPANY_FACTS.format(cik=cik), "facts_" + cik, max_age=86400)

    def latest_10k(self, cik):
        """URL and date of the most recent 10-K, or None."""
        data = self._get(SUBMISSIONS.format(cik=cik), "sub_" + cik, max_age=86400)
        if not data:
            return None
        recent = data.get("filings", {}).get("recent", {})
        for i, form in enumerate(recent.get("form", [])):
            if form != "10-K":
                continue
            acc = recent["accessionNumber"][i].replace("-", "")
            doc = recent["primaryDocument"][i]
            # int() strips the zero-padding; the Archives path uses the bare CIK.
            return {
                "url": "https://www.sec.gov/Archives/edgar/data/{}/{}/{}".format(
                    int(cik), acc, doc
                ),
                "date": recent["filingDate"][i],
                "accession": recent["accessionNumber"][i],
            }
        return None

    def raw(self, url, key):
        """Fetch and cache a document body. Filings are immutable once filed,
        so the cache never needs invalidating."""
        path = CACHE / (key + ".txt.gz")
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return fh.read()

        wait = self.interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        resp = self.session.get(url, timeout=120)
        self._last = time.time()
        resp.raise_for_status()
        text = resp.text

        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
        return text
