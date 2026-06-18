"""
periods.py — HARDENED XBRL period-selection (acceptance fix #4).

The XBRL companyfacts fact-list interleaves:
  - YTD / cumulative rows (e.g. 9-month) alongside discrete-quarter rows,
  - duplicate FY rows (same fy/fp filed in multiple forms / restatements),
  - 8-K and 10-Q/10-K rows mixed.

F1 (2nd-difference of YoY growth) and F2 (margin slope) are the most period-
sensitive signals — feed them YTD-mixed-with-quarter rows and you get "confident
garbage." This module isolates clean comparable series:

  quarterly_series(rows)  -> list of (end_date, val) for DISCRETE ~quarter periods,
                             deduped (latest-filed wins), sorted ascending.
  annual_series(rows)     -> list of (end_date, val) for FY periods, deduped.
  latest_instant(rows)    -> single most-recent point-in-time value (cash, shares).

Discrete-quarter detection: a flow fact (revenue, OCF) has start+end; we keep rows
whose span is ~80–100 days (one fiscal quarter). YTD rows (span ~180/270/365d) are
dropped for the quarterly series. Instant facts (cash, shares) have only `end`.
"""
from __future__ import annotations

import datetime as _dt
from typing import List, Tuple


def _parse(d: str):
    try:
        return _dt.date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _span_days(row) -> int:
    s, e = _parse(row.get("start", "")), _parse(row.get("end", ""))
    if s is None or e is None:
        return -1
    return (e - s).days


def quarterly_series(rows: List[dict]) -> List[Tuple[_dt.date, float]]:
    """Discrete fiscal-quarter flows only. Dedup on end-date, latest filing wins."""
    by_end = {}
    for r in rows:
        span = _span_days(r)
        if span < 0:
            continue
        # discrete quarter ~ 80-100 days; tolerate 75-105 for odd calendars
        if not (75 <= span <= 105):
            continue
        end = _parse(r.get("end", ""))
        val = r.get("val")
        if end is None or not isinstance(val, (int, float)):
            continue
        prev = by_end.get(end)
        # prefer the latest-filed row (10-Q/10-K) for the same end date
        if prev is None or _filed_after(r, prev["_row"]):
            by_end[end] = {"val": float(val), "_row": r}
    out = [(end, by_end[end]["val"]) for end in sorted(by_end)]
    return out


def annual_series(rows: List[dict]) -> List[Tuple[_dt.date, float]]:
    """FY flows (span ~330-380d) OR fp==FY rows. Dedup on fiscal year."""
    by_fy = {}
    for r in rows:
        span = _span_days(r)
        is_fy = (r.get("fp") == "FY") or (330 <= span <= 380)
        if not is_fy:
            continue
        end = _parse(r.get("end", ""))
        val = r.get("val")
        if end is None or not isinstance(val, (int, float)):
            continue
        fy = r.get("fy") or end.year
        prev = by_fy.get(fy)
        if prev is None or _filed_after(r, prev["_row"]):
            by_fy[fy] = {"end": end, "val": float(val), "_row": r}
    out = [(by_fy[fy]["end"], by_fy[fy]["val"]) for fy in sorted(by_fy)]
    return out


def latest_instant(rows: List[dict]) -> Tuple[_dt.date, float]:
    """Most-recent point-in-time value (cash, shares). Returns (None,None) if empty."""
    best = None
    for r in rows:
        end = _parse(r.get("end", ""))
        val = r.get("val")
        if end is None or not isinstance(val, (int, float)):
            continue
        if best is None or end > best[0] or (end == best[0] and _filed_after(r, best[2])):
            best = (end, float(val), r)
    if best is None:
        return (None, None)
    return (best[0], best[1])


def instant_series(rows: List[dict]) -> List[Tuple[_dt.date, float]]:
    """All point-in-time values sorted ascending, deduped on end (latest-filed wins)."""
    by_end = {}
    for r in rows:
        end = _parse(r.get("end", ""))
        val = r.get("val")
        if end is None or not isinstance(val, (int, float)):
            continue
        prev = by_end.get(end)
        if prev is None or _filed_after(r, prev["_row"]):
            by_end[end] = {"val": float(val), "_row": r}
    return [(end, by_end[end]["val"]) for end in sorted(by_end)]


def _filed_after(a: dict, b: dict) -> bool:
    fa, fb = _parse(a.get("filed", "")), _parse(b.get("filed", ""))
    if fa and fb:
        return fa >= fb
    return True  # no filed-date info: accept the later-seen row
