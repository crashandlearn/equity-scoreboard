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
     AMEND-1b CARVE-OUT: a top-3 change caused ONLY by an entry-gate-mandated
     demotion (a FAIL name that was in the prior top-3 and is now correctly pushed
     out) does NOT trip this — else the board would freeze on the stale knife-topped
     version, the opposite of the gate's intent.
  7. ENTRY-TRIGGER GATE (commission 44 A, AMEND-1a; AMEND-2 extends top-2 → top-3):
     rank #1, #2 OR #3 with engine-side `entry_state == "FAIL"` → REJECT (fail-closed).
     The gate reads the DETERMINISTIC entry_state from board.rows[ticker].detail.e —
     NEVER the entry_state the LLM echoes in its ranking row — so a rogue pass cannot
     relabel a knife "PASS" and slip the gate. A FAIL name physically cannot hold a
     deploy-now slot. (Synthetic-suite evidence 48: traps were slipping into #3, the
     uncovered slot; the top-2 bound was insufficient. Kunal-surfaced revert one-liner.)
  8. CORRELATION ENFORCEMENT (AMEND-2, load-bearing — not decorative): if Kairos flags
     a correlated cluster (in kairos_cluster_warnings OR a ranking row's correlation_note),
     NOT ALL members of that cluster may sit in the top-3. If every member of a flagged
     cluster is in the top-3 → REJECT (fail-closed). The warning must change the ordering,
     not merely annotate it. The fix is to demote the lowest-conviction member(s) out of
     the top-3 BEFORE publish; the validator enforces the invariant.

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
# Entry-trigger gate (commission 44 A; AMEND-2 extends top-2 → top-3 on synthetic
# evidence 48 — traps were landing at the uncovered #3 slot). FAIL cannot hold a
# deploy-now slot. The bound is intentionally equal to TOP_K so the gate and the
# degenerate top-3 carve-out cover the same set.
GATE_TOP_SLOTS = 3      # rank #1, #2 AND #3 are the hard-gated deploy-now slots
FAIL_STATE = "FAIL"
# Conviction ordering (best → worst) for picking the lowest-conviction cluster member
# to demote when correlation enforcement must break a fully-clustered top-3.
CONVICTION_ORDER = {tier: i for i, tier in enumerate(CONVICTION_TIERS)}

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


def _tickers_in(text: str, ranked_tickers: set) -> set:
    """
    Extract the set of RANKED tickers a free-text warning/correlation-note refers to.
    We match against the actual ranked-ticker set (not a regex over arbitrary tokens)
    so a stray uppercase word in prose can never fabricate a cluster, and a ticker is
    only counted as a cluster member if it is genuinely in the ranking.
    Word-boundary match so 'NVTS' does not match inside 'NVTSX'.
    """
    if not text:
        return set()
    import re
    found = set()
    for t in ranked_tickers:
        if t and re.search(rf"(?<![A-Z0-9]){re.escape(t)}(?![A-Z0-9])", text):
            found.add(t)
    return found


def _flagged_clusters(clusters, ranking: list, ranked_tickers: set) -> list:
    """
    Build the set of flagged correlated clusters Kairos emitted. A cluster is a SET of
    >=2 ranked tickers named together in EITHER:
      - a kairos_cluster_warnings[] string (each string = one cluster), or
      - a ranking row's correlation_note (the row's own ticker + every other ranked
        ticker the note names → one cluster).
    Returns a list of ticker-sets. Decorative single-name notes (<2 members) are
    ignored — a cluster needs at least two correlated names to be enforceable.
    """
    out = []
    for w in (clusters or []):
        if isinstance(w, str):
            members = _tickers_in(w, ranked_tickers)
            if len(members) >= 2:
                out.append(members)
    for row in ranking:
        note = (row.get("correlation_note") or "").strip()
        if not note:
            continue
        members = _tickers_in(note, ranked_tickers)
        t = row.get("ticker")
        if t in ranked_tickers:
            members = members | {t}      # the row's own name is part of the cluster it notes
        if len(members) >= 2:
            out.append(members)
    return out


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
    # ENGINE-SIDE entry_state, keyed by ticker (AMEND-1a). This is the deterministic
    # Layer-1 value from detail.e — the gate reads THIS, never the LLM's echoed copy.
    entry_state = {
        r["ticker"]: ((r.get("detail") or {}).get("e") or {}).get("entry_state")
        for r in rows
    }

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

    # ── 7. ENTRY-TRIGGER GATE (commission 44 A, AMEND-1a; AMEND-2 top-2 → top-3) ──
    # FAIL cannot hold a deploy-now slot (#1/#2/#3). Reads ENGINE-SIDE entry_state.
    cur_sorted_gate = sorted(ranking, key=lambda r: r.get("kairos_rank", 1e9))
    gate_demoted = set()   # FAIL names the gate would legitimately push out of top-3
    for row in cur_sorted_gate:
        t = row.get("ticker")
        kr = row.get("kairos_rank")
        st = entry_state.get(t)
        if st == FAIL_STATE:
            gate_demoted.add(t)
        if isinstance(kr, int) and not isinstance(kr, bool) and kr <= GATE_TOP_SLOTS:
            if st == FAIL_STATE:
                errs.append(
                    f"{t}: ENTRY-TRIGGER GATE breach — rank #{kr} is a deploy-now slot "
                    f"(top-{GATE_TOP_SLOTS}) but engine-side entry_state=FAIL (falling "
                    f"knife / spent bounce). REJECT (fail-closed, keep last good). "
                    f"A FAIL name cannot sit in the top-{GATE_TOP_SLOTS} of the board.")

    # ── 8. CORRELATION ENFORCEMENT (AMEND-2, load-bearing) ───────────────────
    # If Kairos flags a correlated cluster, NOT ALL of its members may sit in the
    # top-3. A flagged cluster wholly inside the top-3 is the warning being decorative
    # (synthetic S4: warned=True yet all 3 ranked top-3). Fail-closed REJECT — the
    # ordering must demote the lowest-conviction member(s) out of the top-3 first.
    top3_tickers = {
        r.get("ticker")
        for r in cur_sorted_gate
        if isinstance(r.get("kairos_rank"), int) and not isinstance(r.get("kairos_rank"), bool)
        and r.get("kairos_rank") <= TOP_K
    }
    ranked_tickers = {r.get("ticker") for r in ranking}
    for cluster in _flagged_clusters(clusters, ranking, ranked_tickers):
        in_top3 = cluster & top3_tickers
        # breach only if the WHOLE flagged cluster (>=2 members, all of which are in
        # the ranking) sits inside the top-3 — i.e. the warning changed nothing.
        if len(cluster) >= 2 and in_top3 == cluster:
            errs.append(
                f"CORRELATION ENFORCEMENT breach — flagged correlated cluster "
                f"{sorted(cluster)} has ALL {len(cluster)} members in the top-{TOP_K}; "
                f"the warning is decorative, not load-bearing. REJECT (fail-closed): "
                f"demote the lowest-conviction member out of the top-{TOP_K}.")

    # ── 6. DEGENERATE tripwire (amend C) + AMEND-1b carve-out ────────────────
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

        # top-3-SPECIFIC change: the top-3 SET changed (any name in/out of top-3).
        # AMEND-1b CARVE-OUT: if every name that LEFT the prior top-3 is a FAIL name
        # the entry-gate mandated out of a deploy slot, the change is gate-driven, not
        # degenerate — do NOT fire (else the board freezes on the stale knife-topped
        # version). Only the legitimately-removed knives are carved; any OTHER top-3
        # churn still trips.
        if cur_top3 != prev_top3:
            left = prev_top3 - cur_top3                  # names pushed out of top-3
            non_gate_left = left - gate_demoted          # left for a NON-gate reason
            entered = cur_top3 - prev_top3               # names that came in
            # carve-out applies only when EVERY departure is a gate-mandated FAIL
            # demotion AND nothing un-explained entered beyond filling those slots.
            gate_only = bool(left) and not non_gate_left and len(entered) <= len(left)
            if not gate_only:
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
          f"bounds + big-move discipline + entry-trigger gate (top-3) + correlation "
          f"enforcement + degenerate tripwire (gate carve-out applied) all hold.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
