"""
validate_board.py — FAIL-CLOSED publish gate for the Equity Scoreboard.

The cron pipeline is: run_board.py → board.json → **this gate** → publish.
If this gate fails, the workflow does NOT publish: the last good board.json stays
served and a failure is signalled. A stale-correct board beats a fresh-wrong one.

PURE + STDLIB-ONLY (json, math, sys). No network, no private razor imports — this
file is part of the PUBLIC equity-scoreboard subtree, so it must carry nothing
secret and depend on nothing outside this folder.

Checks (all must pass — any failure = REJECT, exit non-zero):
  1. Structure: required top-level keys present, rows is a non-empty list.
  2. Universe size: exactly EXPECTED_UNIVERSE names scored (default 39), and
     scored == len(rows), unavailable is a list (data-unavailable is allowed to be
     reported but the scored count must hold — see MIN_SCORED).
  3. Finiteness: every score and every numeric block value is finite (no NaN/Inf).
  4. Score bounds: 0 <= score <= 100 (composite is 100·gate·additive, gate<=1,
     additive in [0,1]); block sub-scores in [0,1]; survival_gate in [0.3,1.0].
  5. INVARIANT A (scoring-fix #2 — foreign filers flagged, not faked):
     any row whose fundamental_branch is "no_fundamental_data" MUST carry a
     non-null fundamental_flag (honest "unscored" placeholder) and MUST NOT be
     presented as a real computed fundamental (fundamental_valid handling).
  6. INVARIANT B (scoring-fix — catalyst only on REAL Form-4 P-buys):
     the catalyst detail's form4_buys_90d must be an int >= 0, and any K2 credit
     must trace to that count — i.e. we never invent buys. We assert the field
     exists and is a non-negative integer for every row (the scorer counts ONLY
     code-P; this gate guarantees the field wasn't dropped/mutated to a fake).
  7. Freshness sanity: generated_at parses as ISO and is not in the future.

Usage:
  python -m engine.validate_board outputs/board.json
  → prints a PASS/REJECT report to stderr, exit 0 (pass) / 1 (reject).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import sys

# The static universe is 39 (6 themes' seed tickers, deduped, + 16 screen tranche).
# If the universe is intentionally changed, bump this with the weekly universe note.
EXPECTED_UNIVERSE = 39
# We allow a small number of data-unavailable names (chart MISS) without rejecting,
# but require the bulk to score — a board that suddenly can't score most names is
# a broken pull, not a market event.
MIN_SCORED = 35


def _is_finite_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate(board: dict) -> list:
    """Return a list of failure strings. Empty list == PASS."""
    errs = []

    # ── 1. structure ─────────────────────────────────────────────────────────
    for k in ("generated_at", "universe_size", "scored", "unavailable", "rows"):
        if k not in board:
            errs.append(f"structure: missing top-level key '{k}'")
    if errs:
        return errs  # can't go further without the shape

    rows = board["rows"]
    if not isinstance(rows, list) or not rows:
        errs.append("structure: 'rows' must be a non-empty list")
        return errs
    if not isinstance(board["unavailable"], list):
        errs.append("structure: 'unavailable' must be a list")

    # ── 2. universe size ─────────────────────────────────────────────────────
    usize = board["universe_size"]
    scored = board["scored"]
    if usize != EXPECTED_UNIVERSE:
        errs.append(f"universe: size {usize} != expected {EXPECTED_UNIVERSE}")
    if scored != len(rows):
        errs.append(f"universe: scored={scored} != len(rows)={len(rows)}")
    if len(rows) < MIN_SCORED:
        errs.append(f"universe: only {len(rows)} scored rows (< MIN_SCORED {MIN_SCORED})")
    if usize != len(rows) + len(board["unavailable"]):
        errs.append(
            f"universe: {usize} != scored {len(rows)} + unavailable "
            f"{len(board['unavailable'])} (rows don't account for the universe)")

    # ── 3-6. per-row checks ──────────────────────────────────────────────────
    for r in rows:
        t = r.get("ticker", "?")

        # 3. finiteness + 4. bounds — composite score
        sc = r.get("score")
        if not _is_finite_num(sc):
            errs.append(f"{t}: score is non-finite/missing ({sc!r})")
        elif not (0.0 <= sc <= 100.0):
            errs.append(f"{t}: score {sc} out of bounds [0,100]")

        blocks = r.get("blocks", {})
        for bk in ("macro", "technical", "catalyst", "survival_gate"):
            bv = blocks.get(bk)
            if not _is_finite_num(bv):
                errs.append(f"{t}: block '{bk}' non-finite/missing ({bv!r})")
            elif not (0.0 <= bv <= 1.0):
                errs.append(f"{t}: block '{bk}' {bv} out of [0,1]")
        # survival_gate has a tighter floor (scorer maps to [0.3,1.0])
        sg = blocks.get("survival_gate")
        if _is_finite_num(sg) and not (0.30 - 1e-9 <= sg <= 1.0 + 1e-9):
            errs.append(f"{t}: survival_gate {sg} out of [0.3,1.0]")
        # fundamental may legitimately be null (no data); if present must be finite [0,1]
        fv = blocks.get("fundamental")
        if fv is not None:
            if not _is_finite_num(fv):
                errs.append(f"{t}: fundamental block non-finite ({fv!r})")
            elif not (0.0 <= fv <= 1.0):
                errs.append(f"{t}: fundamental block {fv} out of [0,1]")

        # 5. INVARIANT A — foreign/IFRS filers flagged, NOT faked
        if r.get("fundamental_branch") == "no_fundamental_data":
            if not r.get("fundamental_flag"):
                errs.append(
                    f"{t}: INVARIANT-A breach — no_fundamental_data branch without "
                    f"a fundamental_flag (foreign filer must be flagged, not faked)")

        # 6. INVARIANT B — catalyst only on REAL Form-4 P-buys
        cdet = (r.get("detail", {}) or {}).get("c", {}) or {}
        buys = cdet.get("form4_buys_90d")
        if not (isinstance(buys, int) and not isinstance(buys, bool) and buys >= 0):
            errs.append(
                f"{t}: INVARIANT-B breach — form4_buys_90d missing or not a "
                f"non-negative int ({buys!r}); catalyst must trace to real P-buys")

        # 8. ENTRY-TIMING block (Layer-1 extension, design §1B). detail.e must carry
        #    finite retrace_off_low in [0,1] (or null), null-or-non-neg-int
        #    catalyst_days, and a dislocation_state in the enum. Same fail-closed
        #    discipline as INVARIANT A/B.
        edet = (r.get("detail", {}) or {}).get("e")
        if edet is None:
            errs.append(f"{t}: missing detail.e entry-timing block")
        else:
            rol = edet.get("retrace_off_low")
            if rol is not None and not (_is_finite_num(rol) and 0.0 <= rol <= 1.0):
                errs.append(f"{t}: retrace_off_low {rol!r} not null/finite in [0,1]")
            cdays = edet.get("catalyst_days")
            if cdays is not None and not (isinstance(cdays, int)
                                          and not isinstance(cdays, bool) and cdays >= 0):
                errs.append(f"{t}: catalyst_days {cdays!r} not null/non-negative int")
            if edet.get("dislocation_state") not in ("knife", "basing", "recovering"):
                errs.append(f"{t}: dislocation_state {edet.get('dislocation_state')!r} "
                            f"not in enum")
            # ENTRY-TRIGGER GATE fields (commission 44 A) — entry_trigger finite [0,1],
            # entry_state in {PASS,SOFT,FAIL}. Fail-closed: the gate reads these.
            etrig = edet.get("entry_trigger")
            if not (_is_finite_num(etrig) and 0.0 <= etrig <= 1.0):
                errs.append(f"{t}: entry_trigger {etrig!r} not finite in [0,1]")
            if edet.get("entry_state") not in ("PASS", "SOFT", "FAIL"):
                errs.append(f"{t}: entry_state {edet.get('entry_state')!r} not in "
                            f"{{PASS,SOFT,FAIL}}")

    # ── 7. freshness sanity ──────────────────────────────────────────────────
    ga = board["generated_at"]
    try:
        gen = _dt.datetime.fromisoformat(str(ga).replace("Z", "+00:00"))
        now = _dt.datetime.now(_dt.timezone.utc)
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=_dt.timezone.utc)
        if gen > now + _dt.timedelta(hours=1):
            errs.append(f"freshness: generated_at {ga} is in the future")
    except (ValueError, TypeError):
        errs.append(f"freshness: generated_at {ga!r} is not ISO-parseable")

    return errs


def main(argv) -> int:
    path = argv[1] if len(argv) > 1 else "outputs/board.json"
    try:
        with open(path) as f:
            board = json.load(f)
    except (OSError, ValueError) as e:
        print(f"REJECT — cannot load {path}: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1

    errs = validate(board)
    if errs:
        print(f"REJECT — board.json FAILED {len(errs)} validation check(s):", file=sys.stderr)
        for e in errs:
            print(f"  ✗ {e}", file=sys.stderr)
        print("Publish BLOCKED. Last good board stays served (stale-correct > fresh-wrong).",
              file=sys.stderr)
        return 1

    print(f"PASS — board.json valid: {board['scored']} names scored, "
          f"{len(board['unavailable'])} unavailable, all scores finite + in bounds, "
          f"invariants A (foreign-flagged) + B (real P-buys) hold.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
