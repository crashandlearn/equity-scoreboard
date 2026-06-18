"""
entry_timing.py — Layer-1 ENTRY-TIMING signals (E1/E2/E3) for the Scoreboard.

PURE FUNCTION of the observe dicts (chart + submissions). No network, no LLM, no
new sources. All three signals are computed from the OHLCV + 52wk arrays that
observe.fetch_chart already returns, plus the EDGAR submissions feed already
pulled — ZERO new endpoint, zero new cost.

WHY THESE EXIST (design 29 §1B): the v2 `technical` block measures how *dislocated*
a name is; it does NOT measure whether the dislocation is *still available*. That
gap is the LEU miss (board scored the $146 floor; it's now $183, +25% spent).
E1/E2/E3 close it.

  E1  retrace_off_low   — fraction of the move off the recent low already spent.
                          0.05 = still at the floor (cheap, deployable);
                          0.80 = bounced, the entry has left. (the LEU fix)
  E2  catalyst proximity — days to the nearest *dated* trigger. HONEST: this is an
                          "est." filing-cadence proxy when no real dated trigger
                          exists (EDGAR publishes no forward calendar for free).
                          The watchlist K3 dated trigger is the only "dated" kind.
  E3  dislocation_state  — knife (still falling) / basing (flat off low) /
                          recovering (rising off low). "don't chase the bounce."

These feed Layer 2 (Kairos) directly as judgement inputs. They do NOT enter the
deterministic mechanical composite (that would require re-tuning un-backtested
weights — design 29 §1C).
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

# E1 trailing window (trading days) — "the move off the recent low".
E1_WINDOW = 60
# E3 slopes
E3_SHORT = 5
E3_LONG = 20
# E2 filing-cadence proxy: median days between 10-Q filings ~ one quarter.
# We estimate the *next* 10-Q as last_10Q_date + ~91d, surfaced as "est.".
FILING_CADENCE_DAYS = 91
# E2 catalyst buckets
HOT_DAYS = 14
WARM_DAYS = 45

# ── ENTRY-TRIGGER GATE (design 39 §1, build commission 44 A) ─────────────────
# SMA window for the structure check S (price reclaiming its 20d mean vs still under).
SMA_WINDOW = 20
# entry_state banding thresholds (VIEW/tunable per design 39 §1B; flagged un-backtested
# until §3's PASS-vs-FAIL forward split runs).
ENTRY_PASS_FLOOR = 0.60
ENTRY_SOFT_FLOOR = 0.30
# e1q piecewise breakpoints (E1 retrace_off_low → entry-quality).
E1Q_FLOOR_HI = 0.15     # E1 <= 0.15: still at the floor → best entry (e1q = 1.0)
E1Q_SPENT_LO = 0.60     # E1 >= 0.60: bounce mostly spent → e1q = 0.10 (the LEU case)
E1Q_SPENT_VAL = 0.10
# E3 state multipliers (knife actively falling → entry NOT confirmed).
STATE_MULT = {"knife": 0.25, "basing": 1.00, "recovering": 0.85}
STRUCTURE_BONUS = 0.10
ENTRY_STATES = ("PASS", "SOFT", "FAIL")


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


# ── E1 — % retraced off the recent low ──────────────────────────────────────
def retrace_off_low(chart: dict, window: int = E1_WINDOW) -> Optional[float]:
    """
    (price - low_Nd) / (high_since_that_low - low_Nd), N=window trailing days.
      0.0 = sitting on the recent low (cheap, entry intact)
      1.0 = at the high of the bounce off that low (entry spent)
    None if history too thin or the window is degenerate (flat).
    """
    c = chart.get("close", [])
    if len(c) < 5:
        return None
    w = c[-window:] if len(c) >= window else c
    low = min(w)
    # the index of the low within the window, then the high SINCE that low
    lo_idx = max(i for i, v in enumerate(w) if v == low)
    high_since = max(w[lo_idx:]) if lo_idx < len(w) else w[-1]
    price = chart.get("price")
    if not isinstance(price, (int, float)):
        price = c[-1]
    span = high_since - low
    if span <= 0:
        # no move off the low at all — entry is fully intact (we're AT the floor)
        return 0.0
    return round(_clip((price - low) / span, 0.0, 1.0), 3)


# ── E3 — dislocation momentum (knife / basing / recovering) ─────────────────
def dislocation_state(chart: dict) -> str:
    """
    Classify the move at the dislocation using short- vs long-window price slope:
      knife      — still falling (recent slope negative, not yet flat off the low)
      basing     — flat >= ~5d off the low (recent slope ~0, near the recent low)
      recovering — rising off the low (recent slope positive)
    Falls back to 'basing' (neutral) on thin history.
    """
    c = chart.get("close", [])
    if len(c) < E3_LONG + 1:
        return "basing"
    short = c[-E3_SHORT:]
    long_ = c[-E3_LONG:]
    # normalised slopes (per-day % change across the window)
    s_short = (short[-1] - short[0]) / short[0] / max(1, len(short) - 1) if short[0] else 0.0
    s_long = (long_[-1] - long_[0]) / long_[0] / max(1, len(long_) - 1) if long_[0] else 0.0
    low = min(long_)
    near_low = (c[-1] - low) / low <= 0.05 if low else False

    # thresholds: ~0.2%/day either way is "flat enough" to be basing
    flat = 0.002
    if s_short < -flat:
        return "knife"          # still actively falling
    if s_short > flat:
        return "recovering"     # rising off the low
    # roughly flat short-term:
    if near_low or s_long < flat:
        return "basing"
    return "recovering"


# ── ENTRY-TRIGGER GATE — entry_trigger score + PASS/SOFT/FAIL state ──────────
def _sma_reclaim(chart: dict, window: int = SMA_WINDOW) -> Optional[bool]:
    """
    Structure check S (design 39 §1B): is price reclaiming its N-day mean?
      True  = price >= SMA(N)  (structure turning — basing/reclaiming)
      False = price < SMA(N)   (still under — falling)
      None  = insufficient history (< window clean bars) → no bonus, no crash.
    Pure function of close[] — zero new pull (AMEND-1c: explicit null/short fallback).
    """
    c = chart.get("close", [])
    if len(c) < window:
        return None
    sma = sum(c[-window:]) / window
    price = chart.get("price")
    if not isinstance(price, (int, float)):
        price = c[-1]
    return price >= sma


def entry_trigger(chart: Optional[dict], e1: Optional[float], e3: Optional[str]) -> dict:
    """
    Compose E1 (retrace_off_low) + E3 (dislocation_state) + SMA20 structure into a
    single deterministic `entry_trigger` ∈ [0,1] and a PASS/SOFT/FAIL `entry_state`.

    THIS IS THE ENGINE-SIDE, DETERMINISTIC value the validator HARD-gate reads
    (AMEND-1a): the gate keys off board.rows[ticker].detail.e.entry_state, NEVER
    the entry_state the LLM echoes back. A rogue LLM pass cannot relabel a knife.

    Pure function of arrays already pulled — zero new data, zero new cost.
    Returns {entry_trigger, entry_state, structure_reclaim} (structure_reclaim is the
    raw S boolean/None for surfacing). Safe on missing inputs → FAIL-closed-ish
    (no chart / unknown E1 → entry_trigger 0.0 unless basing, never a false PASS).
    """
    state = e3 if e3 in STATE_MULT else "basing"
    # e1q piecewise from E1 (sweet spot = bought near the floor, not the top)
    if e1 is None:
        # unknown entry quality — do NOT award a clean entry; treat as spent-ish.
        e1q = E1Q_SPENT_VAL
    elif e1 <= E1Q_FLOOR_HI:
        e1q = 1.0
    elif e1 >= E1Q_SPENT_LO:
        e1q = E1Q_SPENT_VAL
    else:
        e1q = 1.0 - (e1 - E1Q_FLOOR_HI) / (E1Q_SPENT_LO - E1Q_FLOOR_HI)
    state_mult = STATE_MULT[state]
    reclaim = _sma_reclaim(chart) if chart else None
    bonus = STRUCTURE_BONUS if reclaim else 0.0
    trig = _clip(e1q * state_mult + bonus, 0.0, 1.0)

    # banding — knife can NEVER be PASS (state_mult=0.25 caps trig ≤ 0.35; and the
    # explicit knife guard makes the floor structural regardless of E1).
    if state == "knife" or trig < ENTRY_SOFT_FLOOR:
        st = "FAIL"
    elif trig >= ENTRY_PASS_FLOOR:
        st = "PASS"
    else:
        st = "SOFT"
    return {
        "entry_trigger": round(trig, 3),
        "entry_state": st,
        "structure_reclaim": reclaim,
    }


# ── E2 — catalyst proximity (HONEST "est." proxy) ───────────────────────────
def catalyst_proximity(submissions: dict, watchlist_dated: Optional[str] = None) -> dict:
    """
    Returns {catalyst_days: int|null, catalyst_kind: "wl_dated"|"filing_est"|"none",
             catalyst_bucket: "hot"|"warm"|"cold"|null, est: bool}.

    Priority:
      1. A WATCHLIST dated trigger (ISO date) → real dated catalyst (kind=wl_dated).
      2. Otherwise estimate the next 10-Q from the last 10-Q filing date +
         FILING_CADENCE_DAYS → kind=filing_est, est=True (HONEST: this is a cadence
         proxy, not a published date — EDGAR has no free forward calendar).
      3. Neither → kind=none.
    """
    today = _dt.date.today()

    # 1. real dated watchlist trigger
    if watchlist_dated:
        try:
            d = _dt.date.fromisoformat(watchlist_dated)
            days = (d - today).days
            return {
                "catalyst_days": days,
                "catalyst_kind": "wl_dated",
                "catalyst_bucket": _bucket(days),
                "est": False,
            }
        except (ValueError, TypeError):
            pass

    # 2. filing-cadence estimate from the last 10-Q
    last_10q = _last_form_date(submissions, ("10-Q", "10-K"))
    if last_10q is not None:
        nxt = last_10q + _dt.timedelta(days=FILING_CADENCE_DAYS)
        days = (nxt - today).days
        # if the estimate is already in the past, the next one is one cadence on
        while days < 0:
            nxt = nxt + _dt.timedelta(days=FILING_CADENCE_DAYS)
            days = (nxt - today).days
        return {
            "catalyst_days": days,
            "catalyst_kind": "filing_est",
            "catalyst_bucket": _bucket(days),
            "est": True,
        }

    # 3. nothing
    return {"catalyst_days": None, "catalyst_kind": "none",
            "catalyst_bucket": None, "est": False}


def _bucket(days: Optional[int]) -> Optional[str]:
    if days is None:
        return None
    if days <= HOT_DAYS:
        return "hot"
    if days <= WARM_DAYS:
        return "warm"
    return "cold"


def _last_form_date(submissions: dict, forms: tuple) -> Optional[_dt.date]:
    if not submissions:
        return None
    try:
        rec = submissions["filings"]["recent"]
        fs = rec.get("form", [])
        ds = rec.get("filingDate", [])
    except (KeyError, TypeError):
        return None
    best = None
    for f, d in zip(fs, ds):
        if any(f.startswith(x) for x in forms):
            try:
                dt = _dt.date.fromisoformat(d)
            except ValueError:
                continue
            if best is None or dt > best:
                best = dt
    return best


# ── public: build the detail.e block for a single name ──────────────────────
def entry_block(chart: Optional[dict], submissions: Optional[dict],
                watchlist_dated: Optional[str] = None) -> dict:
    """
    The new `detail.e` block. Safe on missing chart (returns nulls so the
    validator's null-tolerance holds; a name with no chart is data-unavailable
    upstream anyway and never reaches the ranker).
    """
    if not chart:
        # no chart → FAIL-closed entry state (can't confirm an entry on no data;
        # a name with no chart never reaches the ranker anyway).
        et = entry_trigger(None, None, "basing")
        return {
            "retrace_off_low": None,
            "catalyst_days": None,
            "catalyst_kind": "none",
            "catalyst_bucket": None,
            "catalyst_est": False,
            "dislocation_state": "basing",
            "entry_trigger": et["entry_trigger"],
            "entry_state": et["entry_state"],
            "structure_reclaim": et["structure_reclaim"],
        }
    e1 = retrace_off_low(chart)
    e2 = catalyst_proximity(submissions or {}, watchlist_dated)
    e3 = dislocation_state(chart)
    et = entry_trigger(chart, e1, e3)
    return {
        "retrace_off_low": e1,
        "catalyst_days": e2["catalyst_days"],
        "catalyst_kind": e2["catalyst_kind"],
        "catalyst_bucket": e2["catalyst_bucket"],
        "catalyst_est": e2["est"],
        "dislocation_state": e3,
        # ENTRY-TRIGGER GATE (engine-side, deterministic — the validator's gate key)
        "entry_trigger": et["entry_trigger"],
        "entry_state": et["entry_state"],
        "structure_reclaim": et["structure_reclaim"],
    }
