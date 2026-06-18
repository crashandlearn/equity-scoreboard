"""
validate_ranking.py — REPAIR-OR-REJECT gate for the LAYER-2 Kairos ranking.

The pipeline is: run_board → validate_board → kairos_rank → **this gate** → publish.

AMEND-3 (commission 53) — REPAIR, don't REJECT, on the deterministically-fixable
breaches. Kairos's RAW judgment breaches the top-3 gate / correlation rule often
(synthetic ~55%). Under the old REJECT-on-breach design the board kept last-good and
did NOT publish whenever she slipped → frequently STALE / effectively frozen — a
refreshing board that won't refresh (safe but useless). A gate/correlation breach is
DETERMINISTICALLY REPAIRABLE, so we repair the ordering and publish a fresh, gate-clean
board every cycle. Repair is transparent: any name the gate moved carries a `gate_flag`
("gate-demoted: entry not ready" / "correlation-demoted") so the viewer can show it —
no black box.

REJECT → keep-last-good is retained ONLY for truly UNREPAIRABLE failures (can't load /
malformed JSON / schema-invalid / missing required fields / invented names / duplicate
ranks / survival-gated promotion / un-argued big move / degenerate macro churn). Those
are not deterministically fixable by reordering, so they stay fail-closed: the last-good
board stays served and the failure is signalled. Stale-correct beats fresh-wrong (extends
the factor-layer contract in validate_board.py to the ranking layer).

Two-tier model:
  - validate(board) — PURE invariant checker. Returns the list of breaches. Unchanged
    semantics; used by repair() to re-validate (idempotency proof) and by the tests.
  - repair(board)   — deterministic reorder that resolves ONLY the gate + correlation
    breaches, renumbers kairos_rank 1..N contiguously, flags every moved name, and
    leaves the board so validate() reports NO gate/correlation breach. Idempotent.
  - main()          — load → if unrepairable breaches present → REJECT (keep last good);
    else repair → re-validate (must be gate/correlation-clean) → write repaired board
    back → exit 0 (PUBLISH a fresh, gate-clean board).

NOTE: the SYNTHETIC suite (synth_validate.py) scores the RAW rank() output, PRE-repair,
on purpose — so it keeps honestly measuring raw judgment. Repair is NEVER used to make
the synthetic pass. Do not wire repair into synth_validate.

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
GATE_ELIGIBLE = ("PASS", "SOFT")   # entry_states that MAY sit in a deploy-now slot
# Conviction ordering (best → worst) for picking the lowest-conviction cluster member
# to demote when correlation enforcement must break a fully-clustered top-3.
CONVICTION_ORDER = {tier: i for i, tier in enumerate(CONVICTION_TIERS)}

# ── repair flag reasons (surfaced on the row so the viewer is not a black box) ──
FLAG_GATE = "gate-demoted: entry not ready"
FLAG_CORR = "correlation-demoted"

# Substrings that mark a breach the repair pass can DETERMINISTICALLY fix by reorder.
# Everything NOT matching these is treated as UNREPAIRABLE → fail-closed REJECT.
REPAIRABLE_MARKERS = ("ENTRY-TRIGGER GATE breach", "CORRELATION ENFORCEMENT breach")

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
        # AMEND-3 EXTENSION (commission 53): a name the REPAIR pass moved out of the
        # top-3 carries a `gate_flag` (gate- or correlation-demoted). Those departures
        # are deterministic repair, not model drift, so they are carved out too — else
        # re-validating the repaired board would falsely trip DEGENERATE on the very
        # change repair just made. This only affects already-flagged (repaired) boards;
        # a raw pre-repair board has no flags, so legacy behaviour is unchanged.
        repair_demoted = {r.get("ticker") for r in ranking if r.get("gate_flag")}
        carved = gate_demoted | repair_demoted
        if cur_top3 != prev_top3:
            left = prev_top3 - cur_top3                  # names pushed out of top-3
            non_gate_left = left - carved                # left for a NON-carved reason
            entered = cur_top3 - prev_top3               # names that came in
            # carve-out applies only when EVERY departure is a gate-mandated FAIL
            # demotion OR a repair demotion, AND nothing un-explained entered beyond
            # filling those slots.
            gate_only = bool(left) and not non_gate_left and len(entered) <= len(left)
            if not gate_only:
                errs.append(
                    f"DEGENERATE — top-{TOP_K} set changed "
                    f"({sorted(prev_top3)} -> {sorted(cur_top3)}); a top-3-specific "
                    f"change is treated as degenerate. REJECT (keep last good).")

    return errs


# ════════════════════════════════════════════════════════════════════════════
# REPAIR (commission 53) — deterministically resolve the gate + correlation
# breaches and publish fresh, instead of REJECT-and-freeze.
# ════════════════════════════════════════════════════════════════════════════
def _is_repairable(errs: list) -> bool:
    """True iff EVERY breach in `errs` is one the reorder pass can deterministically
    fix (a top-3 gate breach or a correlation breach). If any breach is something else
    — invented name, duplicate rank, survival-gated promotion, un-argued big move,
    degenerate churn, structure fault — the board is NOT deterministically repairable
    and must fail-closed (REJECT, keep last good)."""
    if not errs:
        return True
    return all(any(m in e for m in REPAIRABLE_MARKERS) for e in errs)


def _renumber(ranking: list) -> None:
    """Renumber kairos_rank 1..N contiguously from current list order, and keep the
    `delta` field (mechanical_rank - kairos_rank) consistent so downstream consumers
    and the big-move check see a coherent post-repair ranking."""
    for i, row in enumerate(ranking, 1):
        row["kairos_rank"] = i
        mr = row.get("mechanical_rank")
        if isinstance(mr, int) and not isinstance(mr, bool):
            row["delta"] = mr - i


def _flag(row: dict, reason: str) -> None:
    """Surface a repair flag on the row WITHOUT clobbering an earlier one (a name can
    be both gate- and correlation-demoted). The viewer reads `gate_flag`."""
    existing = row.get("gate_flag")
    if existing and reason not in existing:
        row["gate_flag"] = f"{existing}; {reason}"
    elif not existing:
        row["gate_flag"] = reason


def _gate_repair(ranking: list, entry_state: dict) -> bool:
    """GATE REPAIR — if a name whose ENGINE-SIDE entry_state == FAIL sits in the top-3,
    demote it to just AFTER the last gate-eligible (PASS/SOFT) name, preserving the
    relative order of the others, and pull the next non-FAIL name up into the vacated
    slot. After this, the top-3 contains NO FAIL name. Returns True if anything moved.

    Deterministic + stable: we partition the current order into (non-FAIL kept in order)
    and (FAIL pushed behind the last gate-eligible name, in their original relative
    order). Reads entry_state ENGINE-SIDE — never the LLM echo."""
    order = sorted(ranking, key=lambda r: r.get("kairos_rank", 1e9))
    top3 = order[:GATE_TOP_SLOTS]
    fail_in_top3 = [r for r in top3 if entry_state.get(r.get("ticker")) == FAIL_STATE]
    if not fail_in_top3:
        return False

    non_fail = [r for r in order if entry_state.get(r.get("ticker")) != FAIL_STATE]
    fail = [r for r in order if entry_state.get(r.get("ticker")) == FAIL_STATE]

    # insertion point = just after the last gate-eligible (PASS/SOFT) name among the
    # non-FAIL set; if there is none, FAIL names sink to the very bottom.
    last_eligible_idx = -1
    for i, r in enumerate(non_fail):
        if entry_state.get(r.get("ticker")) in GATE_ELIGIBLE:
            last_eligible_idx = i
    insert_at = last_eligible_idx + 1   # 0 if no eligible name at all

    new_order = non_fail[:insert_at] + fail + non_fail[insert_at:]
    for r in fail_in_top3:
        _flag(r, FLAG_GATE)

    ranking[:] = new_order
    _renumber(ranking)
    return True


def _conviction_key(row: dict):
    """Sort key: lower = higher conviction (so the WORST member sorts last). Unknown
    tiers sort worst; kairos_rank breaks ties (keep the better-ranked name)."""
    conv = row.get("conviction")
    return (CONVICTION_ORDER.get(conv, len(CONVICTION_TIERS)), row.get("kairos_rank", 1e9))


def _correlation_repair(ranking: list, clusters, ranked_tickers: set) -> bool:
    """CORRELATION REPAIR — for each flagged cluster whose members ALL sit in the top-3,
    keep the single highest-conviction member in the top-3 and demote the lowest-
    conviction member(s) to just below #3, preserving the relative order of everyone
    else. After this, no flagged cluster has ALL members in the top-3. Returns True if
    anything moved. Iterates to a fixed point (one demotion can pull another flagged
    name up into the top-3)."""
    moved = False
    for _ in range(len(ranking) + 1):       # bounded; converges well within N passes
        order = sorted(ranking, key=lambda r: r.get("kairos_rank", 1e9))
        by_ticker = {r.get("ticker"): r for r in order}
        top3 = {r.get("ticker") for r in order[:GATE_TOP_SLOTS]}
        to_demote = set()
        for cluster in _flagged_clusters(clusters, ranking, ranked_tickers):
            if len(cluster) >= 2 and cluster & top3 == cluster:
                # keep the highest-conviction member; demote the rest of this cluster.
                members = sorted((by_ticker[t] for t in cluster if t in by_ticker),
                                 key=_conviction_key)
                for r in members[1:]:
                    to_demote.add(r.get("ticker"))
        if not to_demote:
            break
        kept = [r for r in order if r.get("ticker") not in to_demote]
        demoted = [r for r in order if r.get("ticker") in to_demote]
        # place demoted names just below #3 (after the kept top-3), preserving order.
        new_order = kept[:GATE_TOP_SLOTS] + demoted + kept[GATE_TOP_SLOTS:]
        for r in demoted:
            _flag(r, FLAG_CORR)
        ranking[:] = new_order
        _renumber(ranking)
        moved = True
    return moved


def repair(board: dict, archive_dir: str = "outputs/kairos-rankings") -> dict:
    """Deterministically repair the gate + correlation breaches IN PLACE on board's
    kairos_ranking, flagging every moved name. Gate repair runs first (FAIL names can
    never hold a deploy-now slot), then correlation repair. The repaired ranking is
    re-validated by the caller; repair is idempotent (running it on an already-clean
    board is a no-op).

    Returns a dict report: {'gate_repaired', 'correlation_repaired', 'moved': [tickers]}.
    Does NOT touch the unrepairable invariants — those are caught by validate() and the
    caller rejects before ever calling repair()."""
    ranking = board.get("kairos_ranking") or []
    rows = board.get("rows", [])
    entry_state = {
        r["ticker"]: ((r.get("detail") or {}).get("e") or {}).get("entry_state")
        for r in rows if "ticker" in r
    }
    clusters = board.get("kairos_cluster_warnings")
    ranked_tickers = {r.get("ticker") for r in ranking}

    g = _gate_repair(ranking, entry_state)
    # re-derive ranked_tickers is unnecessary (set of names is unchanged by reorder).
    c = _correlation_repair(ranking, clusters, ranked_tickers)

    moved = sorted({r.get("ticker") for r in ranking if r.get("gate_flag")})
    return {"gate_repaired": g, "correlation_repaired": c, "moved": moved}


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

    # ── UNREPAIRABLE → fail-closed REJECT (keep last good) ────────────────────
    # Any breach that reorder cannot deterministically fix (malformed/missing/
    # invented/duplicate/survival-gated/big-move/degenerate) → do NOT publish.
    if errs and not _is_repairable(errs):
        print(f"REJECT — Kairos ranking has UNREPAIRABLE failure(s) ({len(errs)} check(s)):",
              file=sys.stderr)
        for e in errs:
            print(f"  ✗ {e}", file=sys.stderr)
        print("Publish BLOCKED. Last good board stays served (stale-correct > fresh-wrong).",
              file=sys.stderr)
        return 1

    # ── REPAIRABLE (or already clean) → REPAIR + re-validate + PUBLISH fresh ───
    if errs:
        print(f"REPAIR — {len(errs)} deterministically-fixable breach(es); repairing "
              f"the ordering instead of freezing the board:", file=sys.stderr)
        for e in errs:
            print(f"  · {e}", file=sys.stderr)

    report = repair(board, archive_dir)

    # idempotent re-validate: the repaired board MUST be gate + correlation clean.
    post = validate(board, archive_dir)
    residual = [e for e in post if any(m in e for m in REPAIRABLE_MARKERS)]
    if residual:
        # repair failed to converge — a real bug, never silently publish a dirty board.
        print(f"REJECT — repair did NOT clear the gate/correlation breach(es) "
              f"(repair bug, fail-closed):", file=sys.stderr)
        for e in residual:
            print(f"  ✗ {e}", file=sys.stderr)
        return 1
    # any OTHER residual breach introduced by repair (should never happen) → fail-closed.
    other = [e for e in post if not any(m in e for m in REPAIRABLE_MARKERS)]
    if other and not errs:
        # only treat as fatal if the PRE-repair board was clean (repair must not
        # introduce a fresh non-gate breach). When pre-repair already had a degenerate
        # carve-out path it is handled by validate()'s AMEND-3 carve-out.
        print(f"REJECT — repair introduced an unexpected breach (fail-closed):",
              file=sys.stderr)
        for e in other:
            print(f"  ✗ {e}", file=sys.stderr)
        return 1

    # persist the repaired board so the workflow's `Publish validated board` step
    # serves the gate-clean version (only write if repair actually moved something).
    if report["gate_repaired"] or report["correlation_repaired"]:
        try:
            with open(board_path, "w") as f:
                json.dump(board, f, indent=2)
        except OSError as e:
            print(f"REJECT — could not write repaired board: {e.__class__.__name__}: {e}",
                  file=sys.stderr)
            return 1

    moved = ", ".join(report["moved"]) if report["moved"] else "none"
    print(f"PUBLISH — Kairos ranking gate-clean: {len(board['kairos_ranking'])} names ranked. "
          f"Repair: gate={report['gate_repaired']} correlation={report['correlation_repaired']} "
          f"(moved: {moved}). Top-3 contains NO FAIL name and no flagged cluster fully in "
          f"top-3; bounds + big-move discipline + degenerate tripwire all hold. "
          f"Fresh board published.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
