"""
synth_validate.py — SYNTHETIC JUDGMENT HARNESS for Kairos (design 41, commission 44 C).

A TEST TOOL, NOT a live-board feature. It fabricates a universe of INVENTED tickers
with realistic factor tables, runs the REAL Kairos Opus pass against them, and scores
whether her JUDGMENT fires correctly on pre-registered adversarial scenarios.

WHY SYNTHETIC (the load-bearing point): fictional names = ZERO Opus training-data
contamination. Opus cannot have memorised "GHSTX recovered in Q3" because GHSTX does
not exist. A real-ticker backtest cannot separate "good judgment" from "Opus remembers
how the name actually played out". Synthetic removes the prior at the source.

╔══════════════════════════════════════════════════════════════════════════════╗
║ BOUNDARY — HARD-CODED, CANNOT BE OVERRIDDEN (design 41 §0/§5/§6):             ║
║   This harness PROVES judgment soundness against answers WE define.           ║
║   It DOES NOT, and can NEVER, prove real-market profitability.                ║
║   Its output is "judgment regression: PASS/FAIL" — NEVER a performance claim. ║
║   Every report carries the transfer caveat. The result type is BoundedVerdict ║
║   which physically cannot carry a return/PnL/profitability field.             ║
╚══════════════════════════════════════════════════════════════════════════════╝

COST: fixed bank (~12-16 scenarios) × 3 temp-0 runs ≈ 36-48 real Opus calls ≈ ~$5-6.
Bounded — a fixed scenario bank, NOT a fuzzer. Reuses the existing injectable-client
seam (kairos_rank.rank(board, client=...)) with the REAL Opus client.

Run:  ANTHROPIC_API_KEY=... python -m engine.synth_validate
      python -m engine.synth_validate --dry-run   # generate + self-check, NO Opus spend
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from typing import Optional

from . import equity_score as es
from . import kairos_rank
from . import entry_timing

# ── HARD BOUNDARY CONSTANT — referenced in every report. Do not remove. ──────
TRANSFER_CAVEAT = (
    "JUDGMENT-SOUNDNESS ONLY. This measures whether Kairos's reasoning fires "
    "correctly against synthetic answers WE define. It does NOT measure real-market "
    "profitability — that is the forward live loop (score_proof.py) alone. This "
    "output must NEVER be surfaced as a performance/return/PnL claim."
)

# ── pre-registered thresholds: loaded from a COMMITTED file, never inlined ────
HERE = os.path.dirname(__file__)
THRESHOLDS_PATH = os.path.join(HERE, "..", "synth", "thresholds.json")
SCENARIOS_PATH = os.path.join(HERE, "..", "synth", "scenarios.json")

ROBUSTNESS_RUNS = 3          # 3× temp-0 — claim must hold on all 3 (design 41 §3C)
# decorrelation invariant: pairwise |Pearson r| across factor columns must stay below
# this bound, or the universe is rejected as fingerprint-risky (AMEND-3, asserted).
MAX_FACTOR_CORRELATION = 0.5

# real-world ticker shapes we must NOT collide with (a representative guard list;
# the live board universe is also checked at runtime). Invented tickers are re-rolled
# on any hit (AMEND-3 — collision asserted in code, not just intended).
REAL_TICKER_GUARD = {
    # live/related universe + common large caps — collisions re-roll
    "NVTS", "LEU", "OKLO", "MP", "ASTS", "RKLB", "WOLF", "CCJ", "IREN", "ONDS",
    "NVDA", "AAPL", "MSFT", "GOOG", "AMZN", "TSLA", "META", "AMD", "INTC", "SMCI",
    "PLTR", "GLD", "SLV", "COIN", "MSTR", "GME", "AMC", "F", "GM", "BA", "GE",
}


# ╭───────────────────────────────────────────────────────────────────────────╮
# │ PART 1 — THE SYNTHETIC UNIVERSE GENERATOR                                  │
# ╰───────────────────────────────────────────────────────────────────────────╯
# Invented ticker + theme registers (rhyme with real themes, collide with none).
_FAKE_THEME_REGISTER = [
    ("orbital_compute", "Orbital compute"),
    ("grid_storage_chem", "Grid-storage chemistry"),
    ("sovereign_silicon", "Sovereign-AI silicon"),
    ("fusion_supply", "Fusion fuel supply"),
    ("photonic_net", "Photonic networking"),
    ("bio_actuator", "Synthetic bio-actuators"),
]
_SYLL = ["GH", "VY", "KOR", "ZA", "QN", "BR", "XEL", "FY", "NU", "RA", "PLY", "VOR"]
_TAIL = ["STX", "RNA", "RL", "TON", "DYN", "COR", "VEX", "LUM", "TIK", "QOR"]


def _roll_ticker(rng: random.Random, used: set) -> str:
    for _ in range(200):
        t = rng.choice(_SYLL) + rng.choice(_TAIL)
        t = t[:6].upper()
        if t not in used and t not in REAL_TICKER_GUARD:
            used.add(t)
            return t
    raise RuntimeError("ticker namespace exhausted — widen the syllable register")


def _mechanical_score(blocks: dict) -> float:
    """The REAL composite (es.W weights). The anchor must be the genuine formula
    (design 41 §1A.2) so the test is whether Kairos correctly OVERRIDES it."""
    gate = blocks["survival_gate"]
    add_blocks = {"macro": blocks["macro"], "tech": blocks["technical"],
                  "cat": blocks["catalyst"]}
    if blocks.get("fundamental") is not None:
        add_blocks["fund"] = blocks["fundamental"]
    tw = sum(es.W[k] for k in add_blocks)
    additive = sum(add_blocks[k] * es.W[k] for k in add_blocks) / tw
    return round(100.0 * gate * additive, 1)


def make_name(rng: random.Random, used: set, spec: dict, theme=None) -> dict:
    """
    Build one synthetic row in the EXACT _factor_record shape (design 41 §1A) so
    Kairos cannot distinguish synthetic from live. `spec` overrides factor draws to
    force the scenario's conflict; unspecified factors are drawn from realistic
    distributions. mechanical_rank is stamped later (per-universe).
    """
    def draw(key, a, b):
        if key in spec:
            return spec[key]
        return round(rng.betavariate(a, b), 3)

    macro = spec.get("macro", round(rng.uniform(0.5, 0.9) if theme else 0.40, 3))
    fundamental = draw("fundamental", 2, 2)
    technical = draw("technical", 2, 3)
    catalyst = draw("catalyst", 1.5, 3)
    # survival gate: clip into [0.30,1.0]; scenarios force the gated / danger tail.
    sg = spec.get("survival_gate", round(0.30 + 0.70 * rng.betavariate(2, 1.2), 3))
    sg = max(0.30, min(1.0, sg))

    blocks = {"macro": macro, "fundamental": fundamental, "technical": technical,
              "catalyst": catalyst, "survival_gate": sg}
    e1 = spec.get("E1", round(rng.betavariate(2, 2), 3))
    e3 = spec.get("E3", rng.choices(["knife", "basing", "recovering"],
                                    weights=[0.25, 0.45, 0.30])[0])
    cat_bucket = spec.get("catalyst_bucket",
                          rng.choices(["hot", "warm", "cold"], weights=[0.2, 0.3, 0.5])[0])
    cat_days = {"hot": rng.randint(2, 14), "warm": rng.randint(15, 45),
                "cold": rng.randint(46, 200)}[cat_bucket]
    # engine-side entry_trigger/state from the synthetic E1/E3 (no chart → SMA bonus off)
    et = entry_timing.entry_trigger(None, e1, e3)

    ticker = spec.get("ticker")
    if ticker:
        used.add(ticker)          # reserve pinned names so filler can't re-roll them
    else:
        ticker = _roll_ticker(rng, used)
    tid = theme[0] if theme else None
    tname = theme[1] if theme else None
    row = {
        "ticker": ticker,
        "available": True,
        "theme": tid, "theme_name": tname,
        "macro": int(round(macro * 100)) if theme else 40,
        "macro_stale": False,
        "themed": theme is not None,
        "fundamental_branch": spec.get("fundamental_branch", "standard"),
        "fundamental_valid": True,
        "fundamental_flag": None,
        "price": spec.get("price", round(rng.uniform(8, 240), 2)),
        "blocks": blocks,
        "detail": {
            "f": {}, "t": {}, "c": {"form4_buys_90d": 0}, "q": {},
            "e": {
                "retrace_off_low": e1,
                "catalyst_days": cat_days,
                "catalyst_kind": "filing_est",
                "catalyst_bucket": cat_bucket,
                "catalyst_est": True,
                "dislocation_state": e3,
                "entry_trigger": et["entry_trigger"],
                "entry_state": et["entry_state"],
                "structure_reclaim": None,
            },
        },
        "_spec_tags": spec.get("tags", []),   # scoring metadata; stripped before LLM
    }
    row["score"] = _mechanical_score(blocks)
    row["mechanical_score"] = row["score"]
    return row


def _assert_decorrelated(rows: list) -> Optional[str]:
    """AMEND-3: ASSERT pairwise factor decorrelation in code. Returns an error string
    if any factor pair exceeds MAX_FACTOR_CORRELATION, else None.

    Checked over the GENERATOR-DRAWN rows only (filler — `_spec_tags` empty/absent).
    Pinned scenario 'character' names carry deliberately-conflicting factor overrides
    (that IS the test) — including them would flag intentional conflict as a
    fingerprint. The fingerprint risk lives in the RANDOM draws; that's what we bound.
    """
    drawn = [r for r in rows if not r.get("_spec_tags")]
    # Correlation over a small sample is noise — the invariant is meaningful only on a
    # decent generator pool. Per-scenario filler is small by design (6-12 name
    # universes), so we assert decorrelation on the GENERATOR (gen_pool_decorrelated,
    # tested explicitly), and skip the per-scenario check below the sample floor.
    DECORR_MIN_SAMPLE = 12
    if len(drawn) < DECORR_MIN_SAMPLE:
        return None
    cols = {}
    for k in ("macro", "fundamental", "technical", "catalyst", "survival_gate"):
        cols[k] = [r["blocks"][k] for r in drawn
                   if isinstance(r["blocks"].get(k), (int, float))]
    keys = list(cols)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = cols[keys[i]], cols[keys[j]]
            if len(a) != len(b) or len(a) < 3:
                continue
            r = _pearson(a, b)
            if r is not None and abs(r) > MAX_FACTOR_CORRELATION:
                return (f"decorrelation invariant breached: |corr({keys[i]},{keys[j]})|"
                        f"={abs(r):.2f} > {MAX_FACTOR_CORRELATION}")
    return None


def _pearson(a: list, b: list) -> Optional[float]:
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def gen_pool_decorrelated(seed: int = 999, n: int = 60) -> Optional[str]:
    """
    AMEND-3 (asserted): draw a LARGE pool of generator names (no scenario pins) and
    confirm the factor columns decorrelate. This is where the fingerprint-freedom
    guarantee actually lives — a careless distribution change that re-introduced
    correlation across the generator would fail here. Returns an error string or None.
    """
    rng = random.Random(seed)
    used = set(REAL_TICKER_GUARD)
    theme_pool = list(_FAKE_THEME_REGISTER)
    rows = [make_name(rng, used, {}, theme=theme_pool[i % len(theme_pool)])
            for i in range(n)]
    return _assert_decorrelated([{**r, "_spec_tags": []} for r in rows])


def build_universe(scenario: dict, live_universe: Optional[set] = None) -> list:
    """
    Materialise a scenario's small universe (the characters under test + realistic
    filler). Stamps mechanical_rank via the REAL composite ordering. Asserts
    decorrelation (AMEND-3) and collision-freedom (vs REAL_TICKER_GUARD + live board).
    """
    rng = random.Random(scenario["seed"])
    used = set(REAL_TICKER_GUARD) | (live_universe or set())
    rows = []
    # themed clusters: scenario may pin shared themes for correlation cases
    theme_pool = list(_FAKE_THEME_REGISTER)
    rng.shuffle(theme_pool)

    for nspec in scenario["names"]:
        theme = None
        if nspec.get("theme_idx") is not None:
            theme = _FAKE_THEME_REGISTER[nspec["theme_idx"] % len(_FAKE_THEME_REGISTER)]
        elif nspec.get("themed", True) and theme_pool:
            theme = theme_pool[len(rows) % len(theme_pool)]
        rows.append(make_name(rng, used, nspec, theme=theme))

    # realistic filler so it's not a 2-name toy
    for _ in range(scenario.get("filler", 0)):
        rows.append(make_name(rng, used, {}, theme=theme_pool[len(rows) % len(theme_pool)]))

    # stamp mechanical_rank by the REAL composite ordering
    rows.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["mechanical_rank"] = i

    # ── ASSERT invariants (AMEND-3) ──────────────────────────────────────────
    derr = _assert_decorrelated(rows)
    if derr:
        raise AssertionError(f"[{scenario['id']}] {derr}")
    tickers = [r["ticker"] for r in rows]
    if len(set(tickers)) != len(tickers):
        raise AssertionError(f"[{scenario['id']}] duplicate synthetic ticker")
    collide = set(tickers) & (REAL_TICKER_GUARD | (live_universe or set()))
    if collide:
        raise AssertionError(f"[{scenario['id']}] real/live collision: {collide}")
    return rows


def _board_from_rows(rows: list) -> dict:
    """Wrap rows in the board shape build_messages consumes. Strips scoring metadata
    (`_spec_tags`) so the LLM sees ONLY the live _factor_record fields."""
    clean = []
    for r in rows:
        c = {k: v for k, v in r.items() if k != "_spec_tags"}
        clean.append(c)
    return {
        "generated_at": "2026-06-19T00:00:00Z",
        "universe_size": len(clean), "scored": len(clean),
        "unavailable": [], "weights": es.W, "rows": clean,
    }


# ╭───────────────────────────────────────────────────────────────────────────╮
# │ PART 3 — SCORING (binary per-claim, machine-checkable)                     │
# ╰───────────────────────────────────────────────────────────────────────────╯
def _top_k(ranking: list, k: int) -> set:
    rk = sorted(ranking, key=lambda r: r.get("kairos_rank", 1e9))
    return {r["ticker"] for r in rk[:k]}


def _rank_of(ranking: list, ticker: str) -> Optional[int]:
    for r in ranking:
        if r.get("ticker") == ticker:
            return r.get("kairos_rank")
    return None


def check_claim(scenario: dict, out: dict, rows: list) -> tuple[bool, str]:
    """
    Evaluate the scenario's ONE machine-checkable pass condition against a Kairos
    output. Binary per claim (design 41 §3A). Returns (passed, detail).
    """
    claim = scenario["claim"]
    kind = claim["type"]
    ranking = out.get("ranking", [])
    gated = {g.get("ticker") for g in out.get("gated_out", [])}
    ranked_tickers = {r.get("ticker") for r in ranking}
    universe = {r["ticker"] for r in rows}

    # universal sanity floor (applies to every scenario)
    if any(t not in universe for t in ranked_tickers):
        return False, "invented ticker in ranking"
    if all((r.get("conviction") == "LOW") for r in ranking) and ranking:
        return False, "degenerate all-LOW conviction"

    if kind == "trap_out_of_top3":
        t = claim["ticker"]
        top3 = _top_k(ranking, 3)
        ok = t not in top3
        return ok, f"{t} {'NOT in' if ok else 'IN'} top-3"
    if kind == "gated_excluded":
        t = claim["ticker"]
        ok = t not in ranked_tickers and (t in gated or True)
        return ok, f"{t} {'excluded from ranking' if ok else 'PRESENT in ranking'}"
    if kind == "clean_tops":
        t = claim["ticker"]
        top3 = _top_k(ranking, 3)
        ok = t in top3
        if claim.get("must_be_rank1"):
            ok = _rank_of(ranking, t) == 1
        return ok, f"{t} rank={_rank_of(ranking, t)}"
    if kind == "cluster_not_all_top3":
        members = set(claim["members"])
        top3 = _top_k(ranking, 3)
        in_top3 = members & top3
        warned = bool(out.get("cluster_warnings")) or any(
            (r.get("correlation_note") or "").strip()
            for r in ranking if r.get("ticker") in members)
        ok = (len(in_top3) < len(members)) and warned
        return ok, f"{len(in_top3)}/{len(members)} in top3, warned={warned}"
    if kind == "pairwise_order":
        a, b = claim["above"], claim["below"]
        ra, rb = _rank_of(ranking, a), _rank_of(ranking, b)
        ok = ra is not None and rb is not None and ra < rb
        return ok, f"{a}#{ra} vs {b}#{rb}"
    if kind == "sanity":
        # no gated name ranked, no invented ticker, schema valid, not all-LOW
        ok = not (gated & ranked_tickers)
        return ok, "sanity floor"
    return False, f"unknown claim type {kind}"


# ╭───────────────────────────────────────────────────────────────────────────╮
# │ THE BOUNDED VERDICT — cannot carry a profitability field by construction    │
# ╰───────────────────────────────────────────────────────────────────────────╯
class BoundedVerdict:
    """
    The ONLY result type the harness emits. It carries judgment-soundness fields
    ONLY. Any attempt to set a profitability/return/PnL attribute raises — the
    boundary is enforced in code, not just prose (commission 44 C / design 41 §6).
    """
    _FORBIDDEN = ("return", "pnl", "profit", "alpha", "gain", "performance",
                  "money", "realised", "realized")
    __slots__ = ("per_archetype", "robust_pass_rate", "hard_fail", "verdict",
                 "caveat", "scenarios")

    def __init__(self):
        self.per_archetype = {}
        self.robust_pass_rate = 0.0
        self.hard_fail = []
        self.verdict = "PENDING"
        self.caveat = TRANSFER_CAVEAT
        self.scenarios = []

    def __setattr__(self, k, v):
        lk = k.lower()
        if any(bad in lk for bad in self._FORBIDDEN):
            raise AttributeError(
                f"BoundedVerdict refuses field '{k}' — synthetic output can NEVER be a "
                f"profitability/performance claim (boundary guard, commission 44 C).")
        object.__setattr__(self, k, v)

    def to_dict(self) -> dict:
        return {
            "BOUNDARY": self.caveat,
            "verdict": self.verdict,
            "robust_conflict_pass_rate": round(self.robust_pass_rate, 4),
            "per_archetype": self.per_archetype,
            "hard_fail": self.hard_fail,
            "scenarios": self.scenarios,
        }


# ╭───────────────────────────────────────────────────────────────────────────╮
# │ RUNNER                                                                     │
# ╰───────────────────────────────────────────────────────────────────────────╯
def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def run_suite(*, client=None, dry_run: bool = False,
              live_universe: Optional[set] = None) -> BoundedVerdict:
    """
    Build every scenario, (optionally) run the REAL Kairos pass 3× temp-0 per
    scenario, score the binary claim with 3-of-3 robustness, aggregate per-archetype,
    and return a BoundedVerdict. dry_run=True builds + self-checks WITHOUT any Opus
    spend (used by tests and pre-flight).
    """
    thresholds = load_json(THRESHOLDS_PATH)
    bank = load_json(SCENARIOS_PATH)["scenarios"]
    verdict = BoundedVerdict()
    arche_pass, arche_total = {}, {}
    conflict_robust_pass, conflict_total = 0, 0
    broken_archetypes = set()

    for sc in bank:
        rows = build_universe(sc, live_universe=live_universe)   # asserts invariants
        board = _board_from_rows(rows)
        arch = sc["archetype"]
        is_control = sc.get("control", False)
        arche_total[arch] = arche_total.get(arch, 0) + 1
        rec = {"id": sc["id"], "archetype": arch, "control": is_control}

        if dry_run:
            # self-check only: confirm the scenario is well-formed + the engine-side
            # entry_state matches what the scenario intends (no Opus call).
            rec["built"] = True
            rec["names"] = len(rows)
            verdict.scenarios.append(rec)
            continue

        runs = []
        for _ in range(ROBUSTNESS_RUNS):
            try:
                out = kairos_rank.rank(board, client=client)
            except Exception as exc:  # noqa: BLE001
                runs.append((False, f"rank error: {exc.__class__.__name__}"))
                continue
            # hard-fail correctness checks (block ship unconditionally)
            ranked = {r.get("ticker") for r in out.get("ranking", [])}
            universe = {r["ticker"] for r in rows}
            gated_floor = {r["ticker"] for r in rows
                           if r["blocks"]["survival_gate"] <= kairos_rank.GATE_FLOOR
                           + kairos_rank.GATE_FLOOR_EPS}
            if ranked - universe:
                verdict.hard_fail.append(f"{sc['id']}: invented ticker")
            if gated_floor & ranked:
                verdict.hard_fail.append(f"{sc['id']}: gated name ranked")
            runs.append(check_claim(sc, out, rows))

        passed_all = all(p for p, _ in runs) and len(runs) == ROBUSTNESS_RUNS
        rec["runs"] = [{"pass": p, "detail": d} for p, d in runs]
        rec["robust_pass"] = passed_all
        verdict.scenarios.append(rec)

        if passed_all:
            arche_pass[arch] = arche_pass.get(arch, 0) + 1
        else:
            broken_archetypes.add(arch)
        if not is_control:
            conflict_total += 1
            if passed_all:
                conflict_robust_pass += 1

    # aggregate
    verdict.per_archetype = {
        a: {"pass": arche_pass.get(a, 0), "total": arche_total[a]}
        for a in arche_total
    }
    if dry_run:
        verdict.verdict = "DRY-RUN — built + invariants asserted, no Opus spend"
        return verdict

    verdict.robust_pass_rate = (conflict_robust_pass / conflict_total
                                if conflict_total else 0.0)
    # ship-bar (design 41 §3D): ≥85% conflict claims robust AND no archetype fully broken
    bar = thresholds["conflict_robust_pass_rate_floor"]
    every_arch_clears = all(arche_pass.get(a, 0) >= 1 for a in arche_total)
    ship = (verdict.robust_pass_rate >= bar and every_arch_clears
            and not verdict.hard_fail)
    verdict.verdict = "PASS" if ship else "FAIL"
    return verdict


def main(argv) -> int:
    dry = "--dry-run" in argv
    if not dry and not os.environ.get("ANTHROPIC_API_KEY"):
        print("REJECT — ANTHROPIC_API_KEY absent; synthetic suite needs the real Opus "
              "client. Use --dry-run to build + self-check without spend.", file=sys.stderr)
        return 1
    try:
        verdict = run_suite(dry_run=dry)
    except AssertionError as e:
        print(f"INVARIANT FAILURE — {e}", file=sys.stderr)
        return 2
    print(json.dumps(verdict.to_dict(), indent=2))
    print(f"\n{TRANSFER_CAVEAT}", file=sys.stderr)
    # exit non-zero on FAIL so CI / a prompt-change gate blocks on regression
    return 0 if verdict.verdict in ("PASS",) or dry else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
