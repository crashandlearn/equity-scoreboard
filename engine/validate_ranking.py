"""
validate_ranking.py — FAIL-CLOSED gate for the LAYER-2 Kairos ranking.

The pipeline is: run_board → validate_board → kairos_rank → **this gate** → publish.
If this gate REJECTs, the workflow does NOT publish: the last-good board stays
served and the failure is signalled. Stale-correct beats fresh-wrong (extends the
factor-layer contract in validate_board.py to the ranking layer).

PURE + STDLIB-ONLY (json, sys, datetime). No network, no private imports — this
file ships in the PUBLIC subtree.

Checks (all must pass — any failure = REJECT, exit non-zero):
  1. Structure: kairos_ranking / kairos_gated_out / kairos_cluster_warnings present
     and well-typed; ranking non-empty.
  2. Universe integrity: every ranked ticker exists in board.rows; NO invented names;
     no duplicate kairos_rank; kairos_rank is a contiguous 1..N.
  3. Survival bound: NO survival-gated name appears in the ranking (a gated-out name
     can never be promoted — design §2A bound 1).
  4. Big-move discipline: any name whose |kairos_rank - mechanical_rank| > 10 carries
     a non-empty rationale that cites a concrete factor (mandatory argued WHY).
  5. Per-row sanity: conviction ∈ tiers, prob_tier ∈ buckets, rationale non-empty.
  6. DEGENERATE tripwire (amend C): compared to the last-good archived top-10 —
     >= 4 of 10 names churned OR a top-3-SPECIFIC change with no matching factor
     move → DEGENERATE → REJECT (keep last good). Macro doesn't move that fast;
     wholesale churn or a top-3 reshuffle is the model off the rails, not the market.

Usage:
  python -m engine.validate_ranking outputs/board.json [outputs/kairos-rankings]
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

from .kairos_rank import (CONVICTION_TIERS, PROB_TIERS, GATE_FLOOR, GATE_FLOOR_EPS,
                          split_universe)

BIG_MOVE = 10           # >10 places vs mechanical needs an argued WHY
# Amend C degenerate thresholds:
CHURN_FLOOR = 4         # >= 4 of last-good top-10 churned out → degenerate
TOP_K = 3               # a change SPECIFIC to the top-3 set → degenerate

# a "concrete factor" citation — the rationale for a big move must mention at least
# one of these tokens (factor names / entry-timing states).
FACTOR_TOKENS = (
    "E1", "E2", "E3", "retrace", "catalyst", "knife", "basing", "recovering",
    "survival", "gate", "macro", "fundamental", "technical", "cluster",
    "correlat", "trap", "dilution", "runway", "floor", "rvol", "rsi", "drawdown",
    "bounce", "off the low", "themed", "screen",
)


def _last_good_top10(archive_dir: str, before_iso: str | None) -> list | None:
    """
    Load the most recent archived ranking's top-10 ticker set, EXCLUDING today's
    file (we compare against the prior good board, not the one we're validating).
    Returns the ordered top-10 ticker list, or None if no prior archive exists.
    """
    if not os.path.isdir(archive_dir):
        return None
    files = sorted(f for f in os.listdir(archive_dir) if f.endswith(".json"))
    today = _dt.date.today().isoformat() + ".json"
    files = [f for f in files if f != today]
    if not files:
        return None
    try:
        with open(os.path.join(archive_dir, files[-1])) as f:
            prev = json.load(f)
        rk = sorted(prev.get("ranking", []), key=lambda r: r.get("kairos_rank", 1e9))
        return [r["ticker"] for r in rk[:10]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def validate(board: dict, archive_dir: str = "outputs/kairos-rankings") -> list:
    errs = []

    # ── 1. structure ─────────────────────────────────────────────────────────
    ranking = board.get("kairos_ranking")
    gated = board.get("kairos_gated_out")
    clusters = board.get("kairos_cluster_warnings")
    if not isinstance(ranking, list) or not ranking:
        errs.append("structure: kairos_ranking missing or empty")
        return errs
    if not isinstance(gated, list):
        errs.append("structure: kairos_gated_out must be a list")
    if not isinstance(clusters, list):
        errs.append("structure: kairos_cluster_warnings must be a list")

    rows = board.get("rows", [])
    universe = {r["ticker"] for r in rows}
    rankable, gated_floor = split_universe(rows)
    gated_tickers = {r["ticker"] for r in gated_floor}
    mech = {r["ticker"]: r.get("mechanical_rank") for r in rows}

    # ── 2. universe integrity ────────────────────────────────────────────────
    seen_ranks = set()
    seen_tickers = set()
    for row in ranking:
        t = row.get("ticker", "?")
        if t not in universe:
            errs.append(f"{t}: ranked ticker not in the universe (invented name)")
        if t in seen_tickers:
            errs.append(f"{t}: duplicate ticker in ranking")
        seen_tickers.add(t)
        kr = row.get("kairos_rank")
        if not isinstance(kr, int) or isinstance(kr, bool):
            errs.append(f"{t}: kairos_rank not an int ({kr!r})")
        elif kr in seen_ranks:
            errs.append(f"{t}: duplicate kairos_rank {kr}")
        seen_ranks.add(kr)
    # contiguous 1..N
    n = len(ranking)
    if seen_ranks and seen_ranks != set(range(1, n + 1)):
        errs.append(f"ranking: kairos_rank not contiguous 1..{n} (got {sorted(seen_ranks)})")

    # ── 3. survival bound — no gated name promoted ───────────────────────────
    for row in ranking:
        if row.get("ticker") in gated_tickers:
            errs.append(f"{row['ticker']}: BOUND breach — survival-gated name in ranking")

    # ── 4. big-move discipline + 5. per-row sanity ───────────────────────────
    for row in ranking:
        t = row.get("ticker", "?")
        conv = row.get("conviction")
        if conv not in CONVICTION_TIERS:
            errs.append(f"{t}: conviction '{conv}' not in {CONVICTION_TIERS}")
        pt = row.get("prob_tier")
        if pt not in PROB_TIERS:
            errs.append(f"{t}: prob_tier '{pt}' not in {PROB_TIERS}")
        rat = (row.get("rationale") or "").strip()
        if not rat:
            errs.append(f"{t}: empty rationale")

        kr = row.get("kairos_rank")
        mr = mech.get(t)
        if isinstance(kr, int) and isinstance(mr, int):
            if abs(kr - mr) > BIG_MOVE:
                # mandatory argued WHY — must cite a concrete factor token
                cited = any(tok.lower() in rat.lower() for tok in FACTOR_TOKENS)
                if not cited:
                    errs.append(
                        f"{t}: BIG-MOVE breach — moved {abs(kr - mr)} places "
                        f"(mech {mr} → kairos {kr}) without a factor-cited rationale")

    # ── 6. DEGENERATE tripwire (amend C) ─────────────────────────────────────
    prev_top10 = _last_good_top10(archive_dir, board.get("kairos_generated_at"))
    if prev_top10:
        cur_sorted = sorted(ranking, key=lambda r: r.get("kairos_rank", 1e9))
        cur_top10 = [r["ticker"] for r in cur_sorted[:10]]
        cur_top3 = set(cur_top10[:TOP_K])
        prev_top3 = set(prev_top10[:TOP_K])

        churned = len(set(prev_top10) - set(cur_top10))
        if churned >= CHURN_FLOOR:
            errs.append(
                f"DEGENERATE — {churned} of last-good top-10 churned out "
                f"(>= {CHURN_FLOOR}); macro doesn't move that fast. REJECT (keep last good).")

        # top-3-SPECIFIC change: the top-3 SET changed (any name in/out of top-3)
        if cur_top3 != prev_top3:
            errs.append(
                f"DEGENERATE — top-{TOP_K} set changed "
                f"({sorted(prev_top3)} -> {sorted(cur_top3)}); a top-3-specific "
                f"change is treated as degenerate. REJECT (keep last good).")

    return errs


def main(argv) -> int:
    board_path = argv[1] if len(argv) > 1 else "outputs/board.json"
    archive_dir = argv[2] if len(argv) > 2 else "outputs/kairos-rankings"
    try:
        with open(board_path) as f:
            board = json.load(f)
    except (OSError, ValueError) as e:
        print(f"REJECT — cannot load {board_path}: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1

    errs = validate(board, archive_dir)
    if errs:
        print(f"REJECT — Kairos ranking FAILED {len(errs)} check(s):", file=sys.stderr)
        for e in errs:
            print(f"  ✗ {e}", file=sys.stderr)
        print("Publish BLOCKED. Last good board stays served (stale-correct > fresh-wrong).",
              file=sys.stderr)
        return 1

    print(f"PASS — Kairos ranking valid: {len(board['kairos_ranking'])} names ranked, "
          f"bounds + big-move discipline + degenerate tripwire all hold.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
