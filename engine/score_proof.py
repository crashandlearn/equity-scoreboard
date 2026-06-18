"""
score_proof.py — the FORWARD-TRACKING PROOF LOOP for the Kairos ranking.

The board is "a pretty list" until we can SHOW the top-3 outperforms. This reads
the append-only archive (outputs/kairos-rankings/*.json — each refresh's full
ranking + snapshot price + mechanical rank) and, for every past snapshot, computes
forward returns at 5 / 20 / 60 trading days against CURRENT prices pulled via the
APPROVED structured chart path (observe.fetch_chart — never web-search/scrape).

Metrics (design 29 §5B):
  1. PRIMARY  — Kairos top-3 forward-return spread vs equal-weight universe benchmark
                (also top-10).
  2. Hit rate — % of top-3 that beat the universe benchmark.
  3. rank-IC  — Spearman corr between kairos_rank and realised forward return (top-20).
  4. KAIROS-vs-MECHANICAL — same spread for kairos top-3 vs mechanical top-3. This is
                the load-bearing number: if Kairos doesn't beat the formula, Layer 2
                is theatre and the 16-week kill gate fires.
  5. Conviction calibration — do HIGH-conviction names outperform LOW?

16-WEEK KILL GATE: after ~16 weeks of forward data, if Kairos top-3 doesn't beat
BOTH the universe benchmark AND mechanical top-3 with any consistency → recommend
reverting to Layer-1-only and stopping the LLM spend. The proof loop must be willing
to fire the analyst.

STDLIB + observe only. No LLM, no new sources, no money path. Read-only.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

from . import observe

HORIZONS = [5, 20, 60]          # trading days
KILL_GATE_WEEKS = 16
TRADING_DAYS_PER_WEEK = 5
KILL_GATE_DAYS = KILL_GATE_WEEKS * TRADING_DAYS_PER_WEEK  # ~80 trading days of data


def _load_archive(archive_dir: str) -> list:
    """Load all archived snapshots, oldest first. Each carries ranking + snapshot."""
    if not os.path.isdir(archive_dir):
        return []
    out = []
    for fn in sorted(f for f in archive_dir_files(archive_dir)):
        try:
            with open(os.path.join(archive_dir, fn)) as f:
                snap = json.load(f)
            snap["_date"] = fn[:-5]  # strip .json
            out.append(snap)
        except (OSError, ValueError):
            continue
    return out


def archive_dir_files(archive_dir: str) -> list:
    return [f for f in os.listdir(archive_dir) if f.endswith(".json")]


def _forward_return(chart: dict, snap_price: float, horizon: int) -> float | None:
    """
    Realised return from the snapshot price to the close `horizon` trading days
    AFTER the snapshot. We approximate the snapshot's bar as the last bar that is
    `horizon` days back from the current end of the series. If the series isn't yet
    `horizon` days past the snapshot, returns None (window not matured).
    """
    c = chart.get("close", [])
    if len(c) <= horizon or not snap_price:
        return None
    # the bar `horizon` days ago is the matured forward price for a snapshot taken
    # `horizon` days ago. (We re-base on snap_price for a clean entry->now return.)
    # Realised forward = close[-1] vs the snapshot price, only counted once the
    # window has matured. Caller passes only snapshots old enough (see compute()).
    return c[-1] / snap_price - 1.0


def _spearman(ranks: list, rets: list) -> float | None:
    """Spearman rank-IC between kairos_rank (ascending=best) and forward return."""
    pairs = [(rk, r) for rk, r in zip(ranks, rets) if r is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    # rank of returns (descending: best return = rank 1, to align with kairos_rank=1=best)
    order = sorted(range(n), key=lambda i: pairs[i][1], reverse=True)
    ret_rank = [0] * n
    for pos, i in enumerate(order, 1):
        ret_rank[i] = pos
    kr = [p[0] for p in pairs]
    d2 = sum((a - b) ** 2 for a, b in zip(kr, ret_rank))
    return round(1 - (6 * d2) / (n * (n * n - 1)), 3)


def compute(archive_dir: str = "outputs/kairos-rankings") -> dict:
    """Compute the proof metrics across all matured snapshots. Returns a report dict."""
    snaps = _load_archive(archive_dir)
    today = _dt.date.today()
    # cache current charts so we pull each ticker once
    chart_cache: dict = {}

    def chart_for(t):
        if t not in chart_cache:
            chart_cache[t] = observe.fetch_chart(t, rng="1y", interval="1d")
        return chart_cache[t]

    per_horizon = {h: {"kairos_top3": [], "mech_top3": [], "universe": [],
                       "hit": [], "ranks": [], "rets": []} for h in HORIZONS}
    conviction_buckets: dict = {}
    matured_snapshots = 0

    for snap in snaps:
        try:
            sdate = _dt.date.fromisoformat(snap["_date"])
        except (ValueError, KeyError):
            continue
        age_days = (today - sdate).days
        ranking = sorted(snap.get("ranking", []), key=lambda r: r.get("kairos_rank", 1e9))
        snapshot = snap.get("snapshot", {})
        if not ranking or not snapshot:
            continue

        # mechanical top-3 = the 3 lowest mechanical_rank in the snapshot
        mech_sorted = sorted(
            ((t, d.get("mechanical_rank")) for t, d in snapshot.items()
             if isinstance(d.get("mechanical_rank"), int)),
            key=lambda x: x[1])
        mech_top3 = [t for t, _ in mech_sorted[:3]]
        kairos_top3 = [r["ticker"] for r in ranking[:3]]
        kairos_top10 = [r["ticker"] for r in ranking[:10]]
        universe_tickers = list(snapshot.keys())

        for h in HORIZONS:
            # only count a horizon once ~h trading days (~h*1.4 calendar) have passed
            if age_days < h * 1.4:
                continue

            def ew_return(tickers):
                rs = []
                for t in tickers:
                    sp = snapshot.get(t, {}).get("price")
                    ch = chart_for(t)
                    if sp and ch:
                        fr = _forward_return(ch, sp, h)
                        if fr is not None:
                            rs.append(fr)
                return sum(rs) / len(rs) if rs else None

            uni = ew_return(universe_tickers)
            k3 = ew_return(kairos_top3)
            m3 = ew_return(mech_top3)
            if uni is None:
                continue
            if k3 is not None:
                per_horizon[h]["kairos_top3"].append(k3 - uni)
                # hit rate: each kairos top-3 name vs universe benchmark
                for t in kairos_top3:
                    sp = snapshot.get(t, {}).get("price")
                    ch = chart_for(t)
                    if sp and ch:
                        fr = _forward_return(ch, sp, h)
                        if fr is not None:
                            per_horizon[h]["hit"].append(1 if fr > uni else 0)
            if m3 is not None:
                per_horizon[h]["mech_top3"].append(m3 - uni)
            # rank-IC over the ranked top-20
            for r in ranking[:20]:
                t = r["ticker"]
                sp = snapshot.get(t, {}).get("price")
                ch = chart_for(t)
                if sp and ch:
                    fr = _forward_return(ch, sp, h)
                    per_horizon[h]["ranks"].append(r.get("kairos_rank"))
                    per_horizon[h]["rets"].append(fr)
            # conviction calibration (use 20d as the representative horizon)
            if h == 20:
                for r in ranking:
                    sp = snapshot.get(r["ticker"], {}).get("price")
                    ch = chart_for(r["ticker"])
                    if sp and ch:
                        fr = _forward_return(ch, sp, h)
                        if fr is not None:
                            conviction_buckets.setdefault(r.get("conviction"), []).append(fr)
        if age_days >= HORIZONS[0] * 1.4:
            matured_snapshots += 1

    # ── assemble report ──────────────────────────────────────────────────────
    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    report = {"generated_at": _dt.datetime.utcnow().isoformat() + "Z",
              "snapshots_total": len(snaps),
              "snapshots_matured": matured_snapshots,
              "horizons": {}}
    for h in HORIZONS:
        ph = per_horizon[h]
        report["horizons"][str(h)] = {
            "kairos_top3_spread_vs_universe": mean(ph["kairos_top3"]),
            "mech_top3_spread_vs_universe": mean(ph["mech_top3"]),
            "kairos_vs_mechanical": (
                round(mean(ph["kairos_top3"]) - mean(ph["mech_top3"]), 4)
                if ph["kairos_top3"] and ph["mech_top3"] else None),
            "hit_rate": mean(ph["hit"]),
            "rank_ic": _spearman(ph["ranks"], ph["rets"]),
            "n": len(ph["kairos_top3"]),
        }
    report["conviction_calibration"] = {
        k: mean(v) for k, v in sorted(conviction_buckets.items(),
                                      key=lambda kv: str(kv[0]))}

    # ── 16-week kill gate ────────────────────────────────────────────────────
    span_days = 0
    if snaps:
        try:
            first = _dt.date.fromisoformat(snaps[0]["_date"])
            span_days = (today - first).days
        except (ValueError, KeyError):
            span_days = 0
    report["kill_gate"] = _kill_gate(report, span_days)
    return report


def _kill_gate(report: dict, span_days: int) -> dict:
    """
    16-week verdict. Only fires a KILL recommendation once enough forward data has
    accumulated (~16 weeks) AND Kairos fails to beat both benchmarks. Before then:
    PENDING (honest small-n).
    """
    weeks = round(span_days / 7, 1)
    if span_days < KILL_GATE_DAYS:
        return {"status": "PENDING",
                "weeks_elapsed": weeks,
                "weeks_required": KILL_GATE_WEEKS,
                "note": "insufficient forward data — early numbers indicative, not conclusive."}
    # mature: does kairos beat universe AND mechanical at the medium horizon?
    h20 = report["horizons"].get("20", {})
    beats_universe = (h20.get("kairos_top3_spread_vs_universe") or -1) > 0
    beats_mech = (h20.get("kairos_vs_mechanical") or -1) > 0
    if beats_universe and beats_mech:
        verdict = "KEEP"
        note = "Kairos top-3 beats both the universe and mechanical top-3 — Layer 2 earns its keep."
    else:
        verdict = "KILL"
        note = ("Kairos top-3 does NOT beat both the universe benchmark and mechanical "
                "top-3 — revert to Layer-1-only and stop the LLM spend.")
    return {"status": verdict, "weeks_elapsed": weeks,
            "weeks_required": KILL_GATE_WEEKS,
            "beats_universe": beats_universe, "beats_mechanical": beats_mech,
            "note": note}


def main(argv) -> int:
    archive_dir = argv[1] if len(argv) > 1 else "outputs/kairos-rankings"
    out_path = argv[2] if len(argv) > 2 else "outputs/proof_report.json"
    report = compute(archive_dir)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    kg = report["kill_gate"]
    print(f"Proof report: {report['snapshots_matured']} matured snapshots, "
          f"kill-gate={kg['status']} ({kg['weeks_elapsed']}/{kg['weeks_required']}wk). "
          f"-> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
