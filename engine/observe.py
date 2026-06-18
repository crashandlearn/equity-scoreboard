"""
observe.py — equities OBSERVE adapter for the Equity Opportunity Scoreboard.

WALLED OFF: read-only. No IBKR, no money-path, no Argus-book import. The ONLY
network it touches is:
  1. Yahoo chart API  — the APPROVED STRUCTURED PATH (v8/finance/chart), NOT scraping,
     NOT yahoo-search. interval=1d&range=1y. Mirrors AB's lib/macro-refresh.ts.
  2. SEC EDGAR        — data.sec.gov companyfacts (XBRL), submissions (8-K/10-Q feed),
     and the Form-4 / Atom insider feed. Free, structured, public.

Prices ONLY via the structured chart endpoint. We NEVER web-search/scrape for a price
(brain HARD RULE feedback_never_web_search_prices.md). meta.regularMarketPrice is the
"current" price; prior close = meta.previousClose (NOT close[-1], which can be a stale
or null current bar).

Everything returns plain dicts; the scorer is a pure function of these dicts.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional

# ── endpoints ───────────────────────────────────────────────────────────────
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"

# AB-style UA for Yahoo; SEC mandates a descriptive UA with contact.
YAHOO_UA = "Mozilla/5.0 (compatible; RazorScoreboard/1.0)"
SEC_UA = "Razor Equity Scoreboard research rzrtrdr@gmail.com"

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
SEC_THROTTLE = 0.12  # SEC asks <=10 req/s; be polite


def _get(url: str, ua: str, timeout: int = 15) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError):
        return None


def _cache_path(name: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


# ── Yahoo chart (APPROVED structured path) ──────────────────────────────────
def fetch_chart(ticker: str, rng: str = "1y", interval: str = "1d") -> Optional[dict]:
    """
    Pull the structured chart. Returns a normalised dict:
      {ticker, price, prev_close, close[], volume[], high[], low[], open[], ts[]}
    price = meta.regularMarketPrice (current), prev_close = meta.previousClose.
    None on any failure (caller treats as data-unavailable, never fabricates).
    """
    url = f"{YAHOO_CHART}{urllib.parse.quote(ticker)}?interval={interval}&range={rng}"
    raw = _get(url, YAHOO_UA)
    if raw is None:
        return None
    try:
        d = json.loads(raw)
        res = d["chart"]["result"][0]
        meta = res.get("meta", {})
        ind = res.get("indicators", {}).get("quote", [{}])[0]
        ts = res.get("timestamp", []) or []
        close = ind.get("close", []) or []
        # drop trailing null bars (current incomplete bar can be null)
        clean = [(t, c, o, h, l, v) for t, c, o, h, l, v in zip(
            ts, close, ind.get("open", []), ind.get("high", []),
            ind.get("low", []), ind.get("volume", [])) if c is not None]
        if not clean:
            return None
        ts_c, close_c, open_c, high_c, low_c, vol_c = map(list, zip(*clean))
        price = meta.get("regularMarketPrice")
        if not isinstance(price, (int, float)):
            price = close_c[-1]
        prev = meta.get("previousClose")
        if not isinstance(prev, (int, float)):
            prev = close_c[-2] if len(close_c) > 1 else close_c[-1]
        return {
            "ticker": ticker, "price": float(price), "prev_close": float(prev),
            "close": [float(x) for x in close_c],
            "open": [float(x) if x is not None else None for x in open_c],
            "high": [float(x) if x is not None else None for x in high_c],
            "low": [float(x) if x is not None else None for x in low_c],
            "volume": [float(x) if x is not None else 0.0 for x in vol_c],
            "ts": ts_c,
            "high_52w": float(meta.get("fiftyTwoWeekHigh") or max(close_c)),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# ── SEC ticker -> CIK map ───────────────────────────────────────────────────
_TICKER_CIK: Optional[dict] = None


def _load_ticker_cik() -> dict:
    global _TICKER_CIK
    if _TICKER_CIK is not None:
        return _TICKER_CIK
    cp = _cache_path("sec_ticker_map.json")
    raw = None
    if os.path.exists(cp) and (time.time() - os.path.getmtime(cp)) < 7 * 86400:
        with open(cp, "rb") as f:
            raw = f.read()
    if raw is None:
        raw = _get(SEC_TICKER_MAP, SEC_UA)
        if raw:
            with open(cp, "wb") as f:
                f.write(raw)
    _TICKER_CIK = {}
    if raw:
        try:
            for row in json.loads(raw).values():
                _TICKER_CIK[str(row["ticker"]).upper()] = int(row["cik_str"])
        except (KeyError, ValueError, TypeError):
            pass
    return _TICKER_CIK


def cik_for(ticker: str) -> Optional[str]:
    cik = _load_ticker_cik().get(ticker.upper())
    return f"{cik:010d}" if cik is not None else None


# ── SEC companyfacts (XBRL) + submissions ───────────────────────────────────
def fetch_companyfacts(ticker: str) -> Optional[dict]:
    cik = cik_for(ticker)
    if cik is None:
        return None
    time.sleep(SEC_THROTTLE)
    raw = _get(SEC_FACTS.format(cik10=cik), SEC_UA)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def fetch_submissions(ticker: str) -> Optional[dict]:
    """Recent filings feed — used for 8-K presence + filing cadence (K1/K3 proxy)."""
    cik = cik_for(ticker)
    if cik is None:
        return None
    time.sleep(SEC_THROTTLE)
    raw = _get(SEC_SUBMISSIONS.format(cik10=cik), SEC_UA)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


# Form-4 transaction codes (SEC Section 16). ONLY P is a genuine bullish signal:
#   P = open-market / private PURCHASE  (insider putting cash in — the K2 intent)
#   S = open-market sale          → bearish / neutral, NEVER bullish
#   A = grant/award (RSU/comp)    → not a conviction buy
#   M = option/derivative exercise→ mechanical, not a conviction buy
#   G = gift, F = tax withholding, C/D/X/etc → not bullish
# K2's design intent is "open-market insider BUYS on a bombed-out name". Counting
# raw Form-4 *filings* is direction-blind and saturates on RSU vesting / selling,
# which inverts the signal. We parse the raw Form-4 XML transaction codes and count
# ONLY code-P purchases.
_FORM4_CACHE_TTL = 14 * 86400  # Form-4 facts are immutable once filed


def _form4_xml_url(cik: str, accession: str, primary_doc: str) -> Optional[str]:
    """Resolve the RAW Form-4 XML URL from a submissions-feed accession row.
    primaryDocument is usually the XSL-rendered path (xslF345X.../foo.xml); the
    raw XML is the same filename without the xsl prefix, in the accession folder.
    """
    acc_nodash = accession.replace("-", "")
    cik_int = str(int(cik))  # strip leading zeros for the Archives path
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"
    # strip an "xslF345X.../" rendering prefix if present → raw .xml filename
    fname = primary_doc.split("/")[-1] if primary_doc else ""
    if not fname.lower().endswith(".xml"):
        return None
    return base + fname


def _parse_form4_codes(xml_text: str) -> list:
    """Extract every non-derivative + derivative transactionCode from a Form-4."""
    import re
    return re.findall(r"<transactionCode>\s*([A-Za-z])\s*</transactionCode>", xml_text)


def _fetch_form4_codes(cik: str, accession: str, primary_doc: str) -> list:
    """Fetch + parse one Form-4's transaction codes, with on-disk caching."""
    cp = _cache_path("form4_" + accession.replace("-", "") + ".json")
    if os.path.exists(cp) and (time.time() - os.path.getmtime(cp)) < _FORM4_CACHE_TTL:
        try:
            with open(cp, "r") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    url = _form4_xml_url(cik, accession, primary_doc)
    if url is None:
        return []
    time.sleep(SEC_THROTTLE)
    raw = _get(url, SEC_UA)
    codes = _parse_form4_codes(raw.decode("utf-8", "ignore")) if raw else []
    try:
        with open(cp, "w") as f:
            json.dump(codes, f)
    except OSError:
        pass
    return codes


def recent_form4_buys(submissions: dict, ticker: str, days: int = 90) -> int:
    """
    K2 signal: count of genuine open-market INSIDER PURCHASES (Form-4 code P) in
    the last `days`. Parses each recent Form-4's raw XML for transaction codes and
    counts only P (open-market/private purchase). Sells (S), grants (A), option
    exercises (M), gifts (G), tax (F) score ZERO — a name with only selling/vesting
    gets no catalyst credit. Direction-aware: this is the fix for the prior
    filing-count proxy that read insider distribution as bullish.
    """
    if not submissions:
        return 0
    cik = cik_for(ticker)
    if cik is None:
        return 0
    try:
        rec = submissions["filings"]["recent"]
        forms = rec.get("form", [])
        dates = rec.get("filingDate", [])
        accs = rec.get("accessionNumber", [])
        pdocs = rec.get("primaryDocument", [])
    except (KeyError, TypeError):
        return 0
    import datetime as _dt
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    buys = 0
    for i, (f, d) in enumerate(zip(forms, dates)):
        if f != "4":
            continue
        try:
            if _dt.date.fromisoformat(d) < cutoff:
                continue
        except ValueError:
            continue
        acc = accs[i] if i < len(accs) else None
        pdoc = pdocs[i] if i < len(pdocs) else None
        if not acc:
            continue
        codes = _fetch_form4_codes(cik, acc, pdoc or "")
        # one filing can report multiple transactions; count P presence as one buy event
        if any(c.upper() == "P" for c in codes):
            buys += 1
    return buys


def recent_8k(submissions: dict, days: int = 30) -> int:
    """K3 proxy: count of 8-K filings in window (material-event cadence)."""
    if not submissions:
        return 0
    try:
        rec = submissions["filings"]["recent"]
        forms = rec.get("form", [])
        dates = rec.get("filingDate", [])
        import datetime as _dt
        cutoff = _dt.date.today() - _dt.timedelta(days=days)
        return sum(
            1 for f, d in zip(forms, dates)
            if f.startswith("8-K") and _safe_after(d, cutoff)
        )
    except (KeyError, TypeError):
        return 0


def _safe_after(d: str, cutoff) -> bool:
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(d) >= cutoff
    except ValueError:
        return False


# ── XBRL fact extraction with FALLBACK TAG-LISTS (acceptance fix #3) ─────────
# Each logical fact maps to an ORDERED list of us-gaap tags; first one that has
# usable data wins. This is the "MP files neither COGS nor R&D under primary tags"
# fix from the design's acceptance criteria.
TAG_FALLBACKS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "cogs": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfServices",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
    ],
    "rnd": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "ocf": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "shares": [
        "CommonStockSharesOutstanding",
        "CommonStockSharesIssued",
        "EntityCommonStockSharesOutstanding",
    ],
}


def extract_concept(facts: dict, logical: str) -> list:
    """
    Return the raw fact-list (units rows) for the first fallback tag that exists.
    Each row is the SEC dict: {start?, end, val, fy, fp, form, frame?}.
    Empty list if no fallback tag carries data.
    """
    if not facts:
        return []
    usg = facts.get("facts", {}).get("us-gaap", {})
    deia = facts.get("facts", {}).get("dei", {})
    for tag in TAG_FALLBACKS.get(logical, []):
        node = usg.get(tag) or deia.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        # prefer USD, then shares, then any single unit
        rows = units.get("USD") or units.get("shares") or (
            next(iter(units.values()), []) if units else [])
        if rows:
            return rows
    return []


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "OKLO"
    c = fetch_chart(t)
    print(f"{t}: price={c['price'] if c else None} prev={c['prev_close'] if c else None} "
          f"bars={len(c['close']) if c else 0} cik={cik_for(t)}")
