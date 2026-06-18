"""
equity_score.py — the deterministic 14-signal scorer for the Equity Scoreboard.

PURE FUNCTION of the observe dicts (chart + companyfacts + submissions + theme).
No network here. No LLM. Reproducible.

Composite (design v2 §4):
  SCORE = 100 × survival_gate × (
            0.30·Macro(A1) + 0.25·Fundamental(F1..F4)
          + 0.25·TechnicalTiming(C1..C5) + 0.20·Catalyst(K2..K3) )
  survival_gate = clip(Q-score, 0.3, 1.0)

THE 4 ACCEPTANCE FIXES (design STATUS banner):
  1. Conditional, re-normalised fundamental sub-score — any F that can't compute is
     DROPPED and the rest re-weighted. Never NaN→0. (build_fundamental)
  2. Pre-revenue proxy branch — for ~$0-revenue names the fundamental score is
     cash-runway / burn-trend / R&D-level, not revenue/margin. (auto-detected)
  3. Fallback XBRL tag-lists — handled in observe.extract_concept.
  4. Hardened period-selection — handled in periods.py.

Acceptance test: OKLO, MP, ASTS, RKLB, WOLF each yield a VALID (non-None, finite)
fundamental sub-score via the pre-revenue branch where needed.
"""
from __future__ import annotations

import math
from typing import Optional

from . import observe
from . import periods

# revenue floor (USD) below which a name is treated as PRE-REVENUE → proxy branch
PRE_REVENUE_REV_FLOOR = 5_000_000.0

# neutral fundamental prior for foreign/IFRS filers (40-F/20-F) with zero us-gaap
# XBRL data — honest "unscored" placeholder, NOT a momentum substitute (FIX 2).
FOREIGN_FILER_NEUTRAL_PRIOR = 0.50


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _norm(x, lo, hi):
    """Linear map [lo,hi] -> [0,1], clipped."""
    if hi == lo:
        return 0.5
    return _clip((x - lo) / (hi - lo), 0.0, 1.0)


# ── A1 MACRO ────────────────────────────────────────────────────────────────
def macro_score(theme: Optional[dict], neutral_prior: int = 40) -> float:
    """0–1. Themed name = theme.macro/100; screen name = neutral_prior/100."""
    if theme and isinstance(theme.get("macro"), (int, float)):
        return _clip(theme["macro"] / 100.0, 0.0, 1.0)
    return neutral_prior / 100.0


# ── F1..F4 FUNDAMENTAL ──────────────────────────────────────────────────────
def f1_rev_accel(facts: dict) -> Optional[float]:
    """2nd-diff of YoY revenue growth. None if insufficient clean quarters."""
    rows = observe.extract_concept(facts, "revenue")
    q = periods.quarterly_series(rows)
    if len(q) < 8:  # need ~2y of quarters for two YoY growths
        return None
    vals = [v for _, v in q]
    # YoY growth series: q[i]/q[i-4]-1
    yoy = []
    for i in range(4, len(vals)):
        if vals[i - 4] and vals[i - 4] != 0:
            yoy.append(vals[i] / vals[i - 4] - 1.0)
    if len(yoy) < 2:
        return None
    accel = yoy[-1] - yoy[-2]  # is growth itself accelerating
    return _norm(accel, -0.30, 0.30)


def f2_gm_trend(facts: dict) -> Optional[float]:
    """Gross-margin slope over last ~4 quarters. None if no COGS or revenue."""
    rev = periods.quarterly_series(observe.extract_concept(facts, "revenue"))
    cogs = periods.quarterly_series(observe.extract_concept(facts, "cogs"))
    if len(rev) < 4 or len(cogs) < 4:
        return None
    cogs_by_end = dict(cogs)
    gm = []
    for end, r in rev:
        if r and r != 0 and end in cogs_by_end:
            gm.append((r - cogs_by_end[end]) / r)
    if len(gm) < 3:
        return None
    gm = gm[-4:]
    slope = (gm[-1] - gm[0]) / max(1, len(gm) - 1)
    return _norm(slope, -0.05, 0.05)


def f3_rnd_intensity(facts: dict, rev_ttm: Optional[float]) -> Optional[float]:
    """R&D / revenue. High = still investing into the convex bet. None if no R&D."""
    rnd = periods.quarterly_series(observe.extract_concept(facts, "rnd"))
    if not rnd:
        return None
    rnd_ttm = sum(v for _, v in rnd[-4:]) if len(rnd) >= 4 else rnd[-1][1] * 4
    if rev_ttm and rev_ttm > PRE_REVENUE_REV_FLOOR:
        intensity = rnd_ttm / rev_ttm
        return _norm(intensity, 0.0, 0.40)
    return None  # pre-revenue: handled by the proxy branch instead


def f4_rel_strength(chart: dict, cohort_returns: list) -> Optional[float]:
    """Name 120d return minus median of theme cohort return. None if no cohort."""
    r = _trailing_return(chart, 120)
    if r is None:
        return None
    if not cohort_returns:
        return _norm(r, -0.40, 0.40)  # no cohort: absolute momentum, softened
    med = sorted(cohort_returns)[len(cohort_returns) // 2]
    return _norm(r - med, -0.40, 0.40)


def _trailing_return(chart: dict, n: int) -> Optional[float]:
    c = chart.get("close", [])
    if len(c) <= n or c[-n - 1] == 0:
        return None
    return c[-1] / c[-n - 1] - 1.0


# ── PRE-REVENUE PROXY BRANCH (acceptance fix #2) ────────────────────────────
def pre_revenue_fundamental(facts: dict) -> Optional[float]:
    """
    For ~$0-revenue names: Fundamental = blend of
      - cash runway (more = better, capped),
      - burn TREND (burning less than before = better),
      - R&D LEVEL (absolute R&D spend = still building = good for a moonshot).
    Re-normalised over whichever components compute. None only if ALL fail.
    """
    comps, weights = [], []

    runway = _cash_runway_quarters(facts)
    if runway is not None:
        comps.append(_norm(runway, 0.0, 12.0)); weights.append(0.45)

    burn_better = _burn_trend(facts)
    if burn_better is not None:
        comps.append(burn_better); weights.append(0.25)

    rnd = periods.quarterly_series(observe.extract_concept(facts, "rnd"))
    if rnd:
        rnd_ttm = sum(v for _, v in rnd[-4:]) if len(rnd) >= 4 else rnd[-1][1] * 4
        # log-scaled R&D level: $1M→low, $200M→high
        lvl = _norm(math.log10(max(rnd_ttm, 1.0)), 6.0, 8.3)
        comps.append(lvl); weights.append(0.30)

    if not comps:
        return None
    tw = sum(weights)
    return sum(c * w for c, w in zip(comps, weights)) / tw


def _cash_runway_quarters(facts: dict) -> Optional[float]:
    """cash ÷ quarterly op-cash-BURN. None if cash missing or company isn't burning."""
    _, cash = periods.latest_instant(observe.extract_concept(facts, "cash"))
    if cash is None:
        return None
    ocf = periods.quarterly_series(observe.extract_concept(facts, "ocf"))
    if not ocf:
        return None
    recent = [v for _, v in ocf[-4:]]
    avg = sum(recent) / len(recent)
    if avg >= 0:
        return 12.0  # cash-flow positive → max runway (not a survival risk)
    return _clip(cash / abs(avg), 0.0, 12.0)


def _burn_trend(facts: dict) -> Optional[float]:
    """Is the op-cash burn improving (less negative) recently vs a year ago?"""
    ocf = periods.quarterly_series(observe.extract_concept(facts, "ocf"))
    if len(ocf) < 5:
        return None
    recent = ocf[-1][1]
    yago = ocf[-5][1]
    # improvement = recent burn less negative than year-ago burn
    improvement = recent - yago
    return _norm(improvement, -50e6, 50e6)


# ── FUNDAMENTAL SUB-SCORE w/ CONDITIONAL RE-WEIGHT (acceptance fix #1) ───────
F_WEIGHTS = {"F1": 0.30, "F2": 0.25, "F3": 0.20, "F4": 0.25}


def build_fundamental(facts: dict, chart: dict, cohort_returns: list):
    """
    Returns (sub_score_0_1, detail_dict). NEVER NaN. Drops any F that can't
    compute and re-normalises the survivors. If revenue is ~$0 (or NO F-signal
    computes), routes to the pre-revenue proxy branch.
    Returns (None, detail) ONLY if both the standard signals AND the proxy fail
    (truly no fundamental data at all).
    """
    # FIX 2 — foreign/IFRS-filer detection (40-F/20-F file IFRS, not us-gaap XBRL).
    # If EDGAR carries NO us-gaap fundamental data at all (revenue AND cash AND ocf
    # rows all empty), this is a foreign/IFRS filer. The old code silently fell
    # through to rel_strength_only (F4 momentum) and let momentum masquerade as a
    # fundamental score (CCJ fake Fund=1.00). Instead, assign a NEUTRAL prior and
    # FLAG it honestly — never fabricate a fundamental from momentum.
    has_fundamental_data = bool(
        observe.extract_concept(facts, "revenue")
        or observe.extract_concept(facts, "cash")
        or observe.extract_concept(facts, "ocf")
    )
    if not has_fundamental_data:
        detail = {
            "rev_ttm": None,
            "pre_revenue": False,
            "components": {},
            "branch": "no_fundamental_data",
            "flag": "fundamental unavailable — foreign/IFRS filer (no us-gaap XBRL)",
            "neutral_prior": FOREIGN_FILER_NEUTRAL_PRIOR,
        }
        return FOREIGN_FILER_NEUTRAL_PRIOR, detail

    rev_ttm = _revenue_ttm(facts)
    pre_rev = (rev_ttm is None) or (rev_ttm <= PRE_REVENUE_REV_FLOOR)

    components = {}
    if not pre_rev:
        components["F1"] = f1_rev_accel(facts)
        components["F2"] = f2_gm_trend(facts)
        components["F3"] = f3_rnd_intensity(facts, rev_ttm)
    components["F4"] = f4_rel_strength(chart, cohort_returns)

    valid = {k: v for k, v in components.items() if v is not None and math.isfinite(v)}

    # Decide branch: pre-revenue OR too few standard signals survived
    standard_valid = {k: v for k, v in valid.items() if k in ("F1", "F2", "F3")}
    use_proxy = pre_rev or (len(standard_valid) == 0)

    detail = {"rev_ttm": rev_ttm, "pre_revenue": pre_rev, "components": components}

    if use_proxy:
        proxy = pre_revenue_fundamental(facts)
        detail["branch"] = "pre_revenue_proxy"
        detail["proxy"] = proxy
        if proxy is None and "F4" in valid:
            # last resort: lean on relative strength alone
            detail["branch"] = "rel_strength_only"
            return valid["F4"], detail
        if proxy is None:
            return None, detail
        # blend proxy (0.7) with F4 rel-strength (0.3) if F4 available
        if "F4" in valid:
            return 0.7 * proxy + 0.3 * valid["F4"], detail
        return proxy, detail

    # standard branch: conditional re-normalised weighted avg over survivors
    detail["branch"] = "standard"
    tw = sum(F_WEIGHTS[k] for k in valid)
    if tw == 0:
        return None, detail
    sub = sum(valid[k] * F_WEIGHTS[k] for k in valid) / tw
    detail["valid_signals"] = list(valid.keys())
    return sub, detail


def _revenue_ttm(facts: dict) -> Optional[float]:
    q = periods.quarterly_series(observe.extract_concept(facts, "revenue"))
    if len(q) >= 4:
        return sum(v for _, v in q[-4:])
    a = periods.annual_series(observe.extract_concept(facts, "revenue"))
    if a:
        return a[-1][1]
    if q:
        return q[-1][1] * 4
    return None


# ── C1..C5 TECHNICAL TIMING ─────────────────────────────────────────────────
def technical_timing(chart: dict):
    """Returns (sub_score_0_1, detail). All chart-derived."""
    c = chart.get("close", [])
    vol = chart.get("volume", [])
    if len(c) < 30:
        return 0.5, {"note": "thin history"}

    high52 = chart.get("high_52w") or max(c)
    last = c[-1]

    # C1 drawdown from 52wk high
    c1 = _clip((high52 - last) / high52, 0.0, 1.0) if high52 else 0.0
    # C2 RVOL: today vs 20d avg
    v20 = sum(vol[-20:]) / 20 if len(vol) >= 20 else (sum(vol) / len(vol) if vol else 0)
    c2_raw = (vol[-1] / v20) if v20 else 1.0
    c2 = _norm(c2_raw, 1.0, 3.0)
    # C3 gap: |last open vs prior close|
    opens = chart.get("open", [])
    c3 = 0.0
    if len(opens) >= 1 and opens[-1] and len(c) >= 2 and c[-2]:
        c3 = _norm(abs(opens[-1] / c[-2] - 1.0), 0.0, 0.10)
    # C4 RSI(14) oversold → higher score when oversold
    rsi = _rsi(c, 14)
    c4 = _norm(35.0 - rsi, -15.0, 15.0) if rsi is not None else 0.5
    # C5 distance below recent 60d base low
    base_lo = min(c[-60:]) if len(c) >= 60 else min(c)
    c5 = _norm((base_lo - last) / base_lo, -0.05, 0.10) if base_lo else 0.0

    sub = 0.35 * c1 + 0.20 * c2 + 0.15 * c3 + 0.15 * c4 + 0.15 * c5
    return sub, {
        "drawdown": round(c1, 3), "rvol": round(c2_raw, 2),
        "gap": round(c3, 3), "rsi": round(rsi, 1) if rsi else None,
    }


def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(-n, 0):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0)); losses.append(max(-ch, 0))
    ag = sum(gains) / n
    al = sum(losses) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


# ── K2..K3 CATALYST ─────────────────────────────────────────────────────────
def catalyst(submissions: dict, ticker: str, watchlist_trigger: bool = False):
    """
    K2 genuine open-market insider BUYS (Form-4 code P) + K3 8-K cadence / watchlist.
    K2 counts ONLY real purchases (direction-aware) — selling/RSU-vesting scores 0.
    Cap re-tuned to 3: open-market insider buys are rare, so even 2-3 clustered buys
    is a strong conviction signal; the old cap of 8 raw filings was trivially
    saturated by routine vesting/selling activity.
    """
    p_buys = observe.recent_form4_buys(submissions, ticker, days=90)
    ek = observe.recent_8k(submissions, days=30)
    k2 = _norm(p_buys, 0, 3)
    k3 = _norm(ek, 0, 4)
    if watchlist_trigger:
        k3 = max(k3, 0.9)
    sub = 0.5 * k2 + 0.5 * k3
    return sub, {"form4_buys_90d": p_buys, "filings8k_30d": ek,
                 "wl_trigger": watchlist_trigger}


# ── Q1..Q2 SURVIVAL GATE (multiplicative) ───────────────────────────────────
def survival_gate(facts: dict):
    """Returns (gate 0.3–1.0, detail). Multiplies the whole score down."""
    runway = _cash_runway_quarters(facts)
    # Q1 runway score
    if runway is None:
        q1 = 0.6  # unknown → mild penalty, not a kill
    else:
        q1 = _norm(runway, 1.0, 8.0)
    # Q2 dilution: YoY share-count change
    q2 = _dilution_score(facts)
    raw = 0.65 * q1 + 0.35 * q2
    gate = _clip(raw, 0.0, 1.0)
    gate = 0.3 + 0.7 * gate  # map [0,1] -> [0.3,1.0]
    return gate, {"runway_q": round(runway, 1) if runway is not None else None,
                  "dilution": round(q2, 2)}


def _dilution_score(facts: dict) -> float:
    """1.0 = no dilution, 0.0 = heavy dilution YoY. Unknown → 0.6."""
    s = periods.instant_series(observe.extract_concept(facts, "shares"))
    if len(s) < 2:
        return 0.6
    # find a ~1y-apart pair
    latest_end, latest_v = s[-1]
    yago = None
    for end, v in s:
        if (latest_end - end).days >= 300:
            yago = (end, v)
    if yago is None or yago[1] == 0:
        return 0.6
    change = latest_v / yago[1] - 1.0
    # 0% change → 1.0 ; +40% dilution → 0.0
    return _norm(-change, -0.40, 0.0)


# ── COMPOSITE ───────────────────────────────────────────────────────────────
W = {"macro": 0.30, "fund": 0.25, "tech": 0.25, "cat": 0.20}


def score_name(name_obs: dict, cohort_returns: list, neutral_prior: int = 40) -> dict:
    """
    name_obs = {ticker, theme(dict|None), chart(dict|None), facts(dict|None),
                submissions(dict|None), watchlist_trigger(bool)}
    Returns the full scored row. chart=None → data-unavailable (no fabrication).
    """
    t = name_obs["ticker"]
    chart = name_obs.get("chart")
    facts = name_obs.get("facts") or {}
    subs = name_obs.get("submissions") or {}
    theme = name_obs.get("theme")

    if chart is None:
        return {"ticker": t, "available": False, "reason": "chart unavailable"}

    a1 = macro_score(theme, neutral_prior)
    fund, fdetail = build_fundamental(facts, chart, cohort_returns)
    tech, tdetail = technical_timing(chart)
    cat, cdetail = catalyst(subs, t, name_obs.get("watchlist_trigger", False))
    gate, gdetail = survival_gate(facts)

    # conditional re-weight at the COMPOSITE level too: if fundamental is None
    # (no EDGAR data at all), drop its weight and re-normalise the additive block.
    blocks = {"macro": a1, "tech": tech, "cat": cat}
    if fund is not None:
        blocks["fund"] = fund
    tw = sum(W[k] for k in blocks)
    additive = sum(blocks[k] * W[k] for k in blocks) / tw

    composite = 100.0 * gate * additive

    return {
        "ticker": t,
        "available": True,
        "score": round(composite, 1),
        "theme": theme["id"] if theme else None,
        "theme_name": theme["name"] if theme else None,
        "macro": theme["macro"] if theme else neutral_prior,
        "macro_stale": name_obs.get("macro_stale", False),
        "themed": theme is not None,
        "price": round(chart["price"], 2),
        "blocks": {
            "macro": round(a1, 3),
            "fundamental": round(fund, 3) if fund is not None else None,
            "technical": round(tech, 3),
            "catalyst": round(cat, 3),
            "survival_gate": round(gate, 3),
        },
        "fundamental_branch": fdetail.get("branch"),
        "fundamental_valid": fund is not None,
        "fundamental_flag": fdetail.get("flag"),
        "detail": {"f": fdetail, "t": tdetail, "c": cdetail, "q": gdetail},
    }
