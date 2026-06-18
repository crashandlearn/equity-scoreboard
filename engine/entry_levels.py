"""
entry_levels.py — ENTRY/EXIT LEVELS for the top-N names (design 39 §2, commission 44 B).

PURE FUNCTION of the `chart` dict observe.fetch_chart already returns (close[]/high[]/
low[]/price/high_52w). ATR(14)-based price / entry-zone / target / stop / R:R /
distance-to-entry, with honesty labels. ZERO new pull, zero new cost, zero new source.

These are SUGGESTED ZONES, not precise calls. The honesty rules are baked in:
  - levels_conviction LOW on wild ATR (vol high relative to price) or thin structure.
  - distance_to_entry > MISSED_ENTRY_GUARD → "entry mostly left — wait or pass" (anti-LEU).
  - R:R < RR_POOR_FLOOR → "poor R:R" flag.
  - snapshot view, not a standing order (naked-book doctrine).

VALIDATOR AMENDS (43):
  - AMEND-2a: ATR null-bar guard — high[]/low[] can carry None; skip null bars, and
    suppress/LOW-conviction if too few clean bars in the window.
  - AMEND-2b: a SINGLE defined stop anchor (recent_low − k·ATR), not a min() of two
    expressions that can invert.

NOT a live-pipeline mutation — these are render-only fields attached to the top-N rows.
PURE + STDLIB-ONLY (math). No network, no LLM, no key.
"""
from __future__ import annotations

import math
from typing import Optional

ATR_WINDOW = 14
RECENT_LOW_WINDOW = 60        # the floor E1 is measured from
SUPPORT_WINDOW = 20           # swing-low cluster
ENTRY_BAND_ATR = 0.5          # entry zone height = 0.5·ATR
TARGET_ATR = 3.0              # target = min(resistance, price + 3·ATR)
STOP_ATR = 1.0                # stop = recent_low − 1·ATR (single anchor, AMEND-2b)
MISSED_ENTRY_GUARD = 0.08     # distance_to_entry > 8% → "entry mostly left"
RR_POOR_FLOOR = 1.0           # R:R < 1.0 → "poor R:R" flag
WILD_ATR_FRAC = 0.10          # ATR/price > 10% → wild name → LOW conviction
MIN_CLEAN_BARS = 8            # < this many clean TR bars in the window → suppress/LOW


def _clean_ohlc(chart: dict):
    """
    Zip close/high/low into clean (prev_close, high, low) TR triples, SKIPPING any bar
    with a None high/low/close (AMEND-2a null-bar guard). Returns the list of
    (high, low, prev_close) for the most-recent contiguous-clean tail.
    """
    close = chart.get("close", []) or []
    high = chart.get("high", []) or []
    low = chart.get("low", []) or []
    n = min(len(close), len(high), len(low))
    triples = []
    for i in range(1, n):
        h, l, pc = high[i], low[i], close[i - 1]
        if h is None or l is None or pc is None:
            continue
        triples.append((float(h), float(l), float(pc)))
    return triples


def atr(chart: dict, window: int = ATR_WINDOW) -> Optional[float]:
    """
    ATR(window) = mean of the last `window` clean True Ranges.
      TR = max(high-low, |high-prev_close|, |low-prev_close|)
    Returns None if fewer than MIN_CLEAN_BARS clean bars exist in the tail (AMEND-2a).
    """
    triples = _clean_ohlc(chart)
    if len(triples) < MIN_CLEAN_BARS:
        return None
    use = triples[-window:]
    trs = [max(h - l, abs(h - pc), abs(l - pc)) for h, l, pc in use]
    if not trs:
        return None
    return sum(trs) / len(trs)


def _recent_low(chart: dict, window: int = RECENT_LOW_WINDOW) -> Optional[float]:
    c = [x for x in (chart.get("close", []) or []) if x is not None]
    if not c:
        return None
    return float(min(c[-window:]))


def _support(chart: dict) -> Optional[float]:
    """Higher of recent_low and the swing-low cluster (lowest close last ~20 bars)."""
    c = [x for x in (chart.get("close", []) or []) if x is not None]
    if not c:
        return None
    rl = _recent_low(chart)
    swing = float(min(c[-SUPPORT_WINDOW:]))
    return max(rl, swing) if rl is not None else swing


def _resistance(chart: dict, price: float) -> Optional[float]:
    """
    Nearest meaningful prior high ABOVE price: the max high of the window BEFORE the
    recent low (the level it broke down from), falling back to high_52w.
    """
    highs = [x for x in (chart.get("high", []) or []) if x is not None]
    closes = [x for x in (chart.get("close", []) or []) if x is not None]
    h52 = chart.get("high_52w")
    if not closes:
        return float(h52) if isinstance(h52, (int, float)) else None
    # index of the recent low within the last RECENT_LOW_WINDOW
    tail = closes[-RECENT_LOW_WINDOW:]
    lo = min(tail)
    lo_idx_in_tail = max(i for i, v in enumerate(tail) if v == lo)
    abs_lo_idx = len(closes) - len(tail) + lo_idx_in_tail
    pre = highs[:abs_lo_idx] if abs_lo_idx > 0 and highs else []
    cand = [h for h in pre if h > price]
    if cand:
        return float(max(cand))
    if isinstance(h52, (int, float)) and h52 > price:
        return float(h52)
    return None


def compute_levels(chart: Optional[dict], *, catalyst_bucket: Optional[str] = None) -> Optional[dict]:
    """
    The four levels + derived R:R / distance + honesty labels for one name.
    Returns None if chart is missing or ATR can't be computed cleanly (AMEND-2a:
    suppress rather than emit garbage). Otherwise a dict ready to attach as `levels`.

    Single stop anchor (AMEND-2b): stop = recent_low − STOP_ATR·ATR. Clean, monotone,
    below the thesis-break floor by a fixed volatility buffer.
    """
    if not chart:
        return None
    price = chart.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        c = [x for x in (chart.get("close", []) or []) if x is not None]
        if not c:
            return None
        price = float(c[-1])
    price = float(price)

    a = atr(chart)
    if a is None or a <= 0:
        return None  # AMEND-2a: too few clean bars → no false-precision levels

    rl = _recent_low(chart)
    support = _support(chart)
    if rl is None or support is None:
        return None
    resistance = _resistance(chart, price)

    entry_lo = max(support, rl)
    entry_hi = entry_lo + ENTRY_BAND_ATR * a
    entry_mid = (entry_lo + entry_hi) / 2.0

    target = price + TARGET_ATR * a
    if resistance is not None:
        target = min(resistance, target)

    # AMEND-2b: single defined stop anchor below the recent-low thesis-break floor.
    stop = rl - STOP_ATR * a

    rr = None
    risk = entry_mid - stop
    reward = target - entry_mid
    if risk > 0:
        rr = reward / risk

    distance_to_entry = (price - entry_hi) / price  # +ve = price above the zone

    # ── honesty labels ──────────────────────────────────────────────────────
    atr_frac = a / price
    flags = []
    conviction = "MODERATE"
    if atr_frac > WILD_ATR_FRAC:
        conviction = "LOW"
        flags.append("wild ATR — wide, unreliable zones")
    if resistance is None:
        conviction = "LOW"
        flags.append("thin structure — resistance undefined")
    if distance_to_entry > MISSED_ENTRY_GUARD:
        flags.append("entry mostly left — wait for a pullback or pass")
    if rr is not None and rr < RR_POOR_FLOOR:
        flags.append("poor R:R")
    if catalyst_bucket == "hot":
        flags.append("catalyst-driven target — re-assess on the event")
    # well-defined structure + sane ATR + clear room → HIGH
    if (conviction == "MODERATE" and resistance is not None
            and distance_to_entry <= MISSED_ENTRY_GUARD
            and rr is not None and rr >= 1.5):
        conviction = "HIGH"

    return {
        "current_price": round(price, 2),
        "atr": round(a, 3),
        "entry_zone": [round(entry_lo, 2), round(entry_hi, 2)],
        "target": round(target, 2),
        "stop": round(stop, 2),
        "rr": round(rr, 2) if rr is not None else None,
        "distance_to_entry": round(distance_to_entry, 4),
        "levels_conviction": conviction,
        "flags": flags,
        "note": ("suggested zones, not precise calls — entry = basing shelf + "
                 "0.5·ATR; stop = thesis break below the recent low"),
    }


def select_level_names(rows: list, kairos_ranking: Optional[list],
                       just_became: Optional[set], *, cap: int = 7) -> list:
    """
    top-5 by kairos_rank ∪ justBecameAttractive, capped ~7 (design 39 §2A).
    `kairos_ranking` is board.kairos_ranking (LLM order); falls back to mechanical_rank
    when absent. `just_became` is a set of tickers the client/engine flagged fresh.
    Returns the ordered, capped ticker list. Levels are expensive attention — spend
    them only where deployment is live.
    """
    order = []
    if kairos_ranking:
        order = [r["ticker"] for r in
                 sorted(kairos_ranking, key=lambda r: r.get("kairos_rank", 1e9))]
    else:
        order = [r["ticker"] for r in
                 sorted(rows, key=lambda r: r.get("mechanical_rank", 1e9))]
    top5 = order[:5]
    sel = list(top5)
    for t in (just_became or set()):
        if t not in sel:
            sel.append(t)
    return sel[:cap]
