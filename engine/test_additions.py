"""
test_additions.py — unit tests for the three additions (commission 44):
  A. ENTRY-TRIGGER GATE — un-bypassability (gate reads ENGINE-SIDE entry_state, not
     the LLM echo) + the AMEND-1b tripwire carve-out.
  B. ENTRY/EXIT LEVELS — ATR math, null-bar guard, single stop anchor, honesty labels.
  C. SYNTHETIC HARNESS — dry-run build, decorrelation + collision asserts, and the
     BoundedVerdict boundary guard (cannot carry a profitability field).

No live key required: the gate/carve-out tests drive validate_ranking directly with
hand-built boards; the synth tests use --dry-run (no Opus spend).

Run:  python -m engine.test_additions
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from . import entry_timing
from . import entry_levels
from . import validate_ranking
from . import validate_board
from . import synth_validate


# ── shared board builder with engine-side entry_state ────────────────────────
def _row(t, mech, gate=0.9, entry_state="PASS", entry_trigger=0.8):
    return {
        "ticker": t, "available": True, "score": 50.0 - mech, "theme": "T1",
        "theme_name": "X", "macro": 80, "macro_stale": False, "themed": True,
        "price": 100.0,
        "blocks": {"macro": 0.8, "fundamental": 0.5, "technical": 0.3,
                   "catalyst": 0.4, "survival_gate": gate},
        "fundamental_branch": "standard", "fundamental_valid": True,
        "fundamental_flag": None,
        "mechanical_rank": mech, "mechanical_score": 50.0 - mech,
        "detail": {"f": {}, "t": {}, "c": {"form4_buys_90d": 0}, "q": {},
                   "e": {"retrace_off_low": 0.1, "catalyst_days": 20,
                         "catalyst_kind": "filing_est", "catalyst_bucket": "warm",
                         "catalyst_est": True, "dislocation_state": "basing",
                         "entry_trigger": entry_trigger, "entry_state": entry_state,
                         "structure_reclaim": None}},
    }


def _board(rows, ranking, **kw):
    b = {"generated_at": "2026-06-19T00:00:00Z", "universe_size": len(rows),
         "scored": len(rows), "unavailable": [], "weights": {}, "rows": rows,
         "kairos_ranking": ranking,
         "kairos_gated_out": kw.get("gated", []),
         "kairos_cluster_warnings": [], "kairos_generated_at": "2026-06-19T00:05:00Z"}
    return b


def _rk(t, kr, mr, rationale="basing, E1 cheap"):
    return {"ticker": t, "kairos_rank": kr, "mechanical_rank": mr, "delta": mr - kr,
            "conviction": "MODERATE", "prob_tier": "~50%", "rationale": rationale,
            "correlation_note": ""}


# ════════════════════════════════════════════════════════════════════════════
# A. ENTRY-TRIGGER GATE
# ════════════════════════════════════════════════════════════════════════════
class EntryTriggerScoreTests(unittest.TestCase):
    def test_knife_is_always_fail(self):
        # knife → FAIL is STRUCTURAL (explicit state guard), regardless of the trigger
        # value or any SMA reclaim bonus. A knife can never hold a deploy-now slot.
        for chart, e1 in [({"close": [100] * 25, "price": 100}, 0.0),   # +SMA bonus
                          ({"close": [100] * 25, "price": 90}, 0.0),    # no bonus
                          ({"close": [100] * 25, "price": 100}, 0.5)]:
            et = entry_timing.entry_trigger(chart, e1=e1, e3="knife")
            self.assertEqual(et["entry_state"], "FAIL")
        # state_mult guarantees trigger stays modest even with the bonus
        self.assertLessEqual(
            entry_timing.entry_trigger({"close": [100] * 25, "price": 90},
                                       e1=0.0, e3="knife")["entry_trigger"],
            0.25 + 1e-9)

    def test_clean_basing_at_floor_is_pass(self):
        # E1 at floor + basing + price>=SMA20 → PASS
        chart = {"close": [50] * 25, "price": 55}  # price above flat mean
        et = entry_timing.entry_trigger(chart, e1=0.05, e3="basing")
        self.assertEqual(et["entry_state"], "PASS")

    def test_spent_bounce_is_not_pass(self):
        et = entry_timing.entry_trigger({"close": [50] * 25, "price": 90},
                                        e1=0.95, e3="recovering")
        self.assertNotEqual(et["entry_state"], "PASS")

    def test_unknown_e1_never_false_pass(self):
        et = entry_timing.entry_trigger(None, e1=None, e3="basing")
        self.assertNotEqual(et["entry_state"], "PASS")


class GateUnbypassabilityTests(unittest.TestCase):
    """The load-bearing test: a FAIL name at #1/#2/#3 is REJECTED (AMEND-2 extended the
    gate top-2 → top-3 on synthetic evidence 48), and the gate reads the ENGINE-SIDE
    entry_state — a rogue LLM echo cannot slip it."""

    def test_fail_at_rank1_rejected(self):
        rows = [_row("KNIFE", 1, entry_state="FAIL"), _row("CLEAN", 2)]
        b = _board(rows, [_rk("KNIFE", 1, 1), _rk("CLEAN", 2, 2)])
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("ENTRY-TRIGGER GATE breach" in e and "KNIFE" in e for e in errs), errs)

    def test_fail_at_rank2_rejected(self):
        rows = [_row("CLEAN", 1), _row("KNIFE", 2, entry_state="FAIL")]
        b = _board(rows, [_rk("CLEAN", 1, 1), _rk("KNIFE", 2, 2)])
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("ENTRY-TRIGGER GATE breach" in e for e in errs), errs)

    def test_fail_at_rank3_rejected(self):
        # AMEND-2: #3 is now a hard-gated deploy-now slot — a FAIL name there REJECTS.
        # This is the exact slot the synthetic suite (S1/S11/S12) found traps slipping
        # into when the gate covered only top-2.
        rows = [_row("CLEAN1", 1), _row("CLEAN2", 2), _row("KNIFE", 3, entry_state="FAIL")]
        b = _board(rows, [_rk("CLEAN1", 1, 1), _rk("CLEAN2", 2, 2),
                          _rk("KNIFE", 3, 3, "wait for the entry — knife, E3=knife")])
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("ENTRY-TRIGGER GATE breach" in e and "KNIFE" in e for e in errs), errs)

    def test_fail_at_rank4_allowed(self):
        # #4 and below remain permitted with the "wait for the entry" flag.
        rows = [_row("CLEAN1", 1), _row("CLEAN2", 2), _row("CLEAN3", 3),
                _row("KNIFE", 4, entry_state="FAIL")]
        b = _board(rows, [_rk("CLEAN1", 1, 1), _rk("CLEAN2", 2, 2), _rk("CLEAN3", 3, 3),
                          _rk("KNIFE", 4, 4, "wait for the entry — knife, E3=knife")])
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertFalse(any("ENTRY-TRIGGER GATE" in e for e in errs), errs)

    def test_gate_reads_engine_side_not_llm_echo(self):
        # Engine-side says KNIFE is FAIL; the LLM ranking row maliciously echoes
        # entry_state=PASS. The gate must STILL reject — it never trusts the echo.
        rows = [_row("KNIFE", 1, entry_state="FAIL"), _row("CLEAN", 2)]
        rk_knife = _rk("KNIFE", 1, 1)
        rk_knife["entry_state"] = "PASS"   # rogue echo — must be ignored
        b = _board(rows, [rk_knife, _rk("CLEAN", 2, 2)])
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("ENTRY-TRIGGER GATE breach" in e for e in errs),
                        "rogue LLM echo of PASS must NOT bypass the engine-side gate")


class CorrelationEnforcementTests(unittest.TestCase):
    """AMEND-2: a flagged correlated cluster cannot have ALL its members in the top-3.
    The warning must change the ORDER, not just annotate it (synthetic S4 fix)."""

    def _clean_rows(self, tickers):
        return [_row(t, i + 1) for i, t in enumerate(tickers)]

    def test_flagged_cluster_all_top3_rejected_via_warnings(self):
        # Three correlated names ALL in the top-3, flagged in kairos_cluster_warnings.
        rows = self._clean_rows(["ZACOR", "ZADYN", "ZARNA", "OTHER"])
        rk = [_rk("ZACOR", 1, 1), _rk("ZADYN", 2, 2), _rk("ZARNA", 3, 3), _rk("OTHER", 4, 4)]
        b = _board(rows, rk)
        b["kairos_cluster_warnings"] = ["ZACOR + ZADYN + ZARNA all orbital_compute — one bet"]
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("CORRELATION ENFORCEMENT breach" in e for e in errs), errs)

    def test_flagged_cluster_all_top3_rejected_via_correlation_note(self):
        # Same breach, but flagged only in the rows' correlation_note fields.
        rows = self._clean_rows(["ZACOR", "ZADYN", "ZARNA", "OTHER"])
        rk = [_rk("ZACOR", 1, 1), _rk("ZADYN", 2, 2), _rk("ZARNA", 3, 3), _rk("OTHER", 4, 4)]
        rk[0]["correlation_note"] = "cluster w/ ZADYN, ZARNA"
        b = _board(rows, rk)
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("CORRELATION ENFORCEMENT breach" in e for e in errs), errs)

    def test_flagged_cluster_one_demoted_passes(self):
        # The fix: demote the lowest-conviction member to #4 → cluster no longer fully
        # in the top-3 → enforcement passes (the warning changed the order).
        rows = self._clean_rows(["ZACOR", "ZADYN", "OTHER", "ZARNA"])
        rk = [_rk("ZACOR", 1, 1), _rk("ZADYN", 2, 2), _rk("OTHER", 3, 3), _rk("ZARNA", 4, 4)]
        b = _board(rows, rk)
        b["kairos_cluster_warnings"] = ["ZACOR + ZADYN + ZARNA all orbital_compute — one bet"]
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertFalse(any("CORRELATION ENFORCEMENT breach" in e for e in errs), errs)

    def test_unflagged_cluster_in_top3_not_enforced(self):
        # No warning + no correlation_note → enforcement does NOT fire. The enforcement
        # is driven by Kairos's OWN flag; it never invents a cluster from nowhere.
        rows = self._clean_rows(["ZACOR", "ZADYN", "ZARNA", "OTHER"])
        rk = [_rk("ZACOR", 1, 1), _rk("ZADYN", 2, 2), _rk("ZARNA", 3, 3), _rk("OTHER", 4, 4)]
        b = _board(rows, rk)  # no cluster_warnings, no notes
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertFalse(any("CORRELATION ENFORCEMENT breach" in e for e in errs), errs)

    def test_two_member_flagged_cluster_both_top3_rejected(self):
        # A 2-name flagged cluster with both in the top-3 is also a breach (one must go).
        rows = self._clean_rows(["ZACOR", "ZADYN", "OTHER"])
        rk = [_rk("ZACOR", 1, 1), _rk("ZADYN", 2, 2), _rk("OTHER", 3, 3)]
        b = _board(rows, rk)
        b["kairos_cluster_warnings"] = ["ZACOR + ZADYN correlated — size as one"]
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("CORRELATION ENFORCEMENT breach" in e for e in errs), errs)

    def test_prose_warning_without_real_tickers_no_false_breach(self):
        # A cluster warning mentioning no ranked ticker (or only one) cannot fabricate
        # a breach — a stray uppercase word in prose must not invent a cluster.
        rows = self._clean_rows(["ZACOR", "ZADYN", "ZARNA", "OTHER"])
        rk = [_rk("ZACOR", 1, 1), _rk("ZADYN", 2, 2), _rk("ZARNA", 3, 3), _rk("OTHER", 4, 4)]
        b = _board(rows, rk)
        b["kairos_cluster_warnings"] = ["AI POWER theme is crowded — WATCH the SPACE"]
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertFalse(any("CORRELATION ENFORCEMENT breach" in e for e in errs), errs)


class TripwireCarveOutTests(unittest.TestCase):
    """AMEND-1b: a gate-mandated demotion of a FAIL knife out of the top-3 must NOT
    trip the degenerate top-3-change tripwire (else the board freezes on the stale
    knife-topped version)."""

    def _archive(self, d, prior_top3):
        prior = {"ranking": [{"ticker": t, "kairos_rank": i + 1}
                             for i, t in enumerate(prior_top3 + ["X4", "X5"])]}
        with open(os.path.join(d, "2026-06-01.json"), "w") as f:
            json.dump(prior, f)

    def test_gate_demotion_does_not_trip_tripwire(self):
        with tempfile.TemporaryDirectory() as d:
            # yesterday the knife was #1 in the top-3; today it's correctly demoted.
            self._archive(d, ["KNIFE", "CLEAN1", "CLEAN2"])
            rows = [_row("CLEAN1", 1), _row("CLEAN2", 2),
                    _row("KNIFE", 3, entry_state="FAIL")]
            # current top-3 = CLEAN1, CLEAN2, + a new name filling the vacated slot
            rows.append(_row("CLEAN3", 4))
            b = _board(rows, [_rk("CLEAN1", 1, 1), _rk("CLEAN2", 2, 2),
                              _rk("CLEAN3", 3, 4),
                              _rk("KNIFE", 4, 3, "wait for the entry — knife")])
            errs = validate_ranking.validate(b, archive_dir=d)
            # the ONLY top-3 departure is the gate-mandated FAIL knife → carve-out fires
            self.assertFalse(any("DEGENERATE" in e and "top-3" in e for e in errs),
                             f"carve-out should suppress the degenerate tripwire: {errs}")

    def test_non_gate_top3_churn_still_trips(self):
        with tempfile.TemporaryDirectory() as d:
            # a CLEAN (non-FAIL) name left the top-3 for no gate reason → still degenerate
            self._archive(d, ["CLEAN1", "CLEAN2", "CLEAN3"])
            rows = [_row("CLEAN1", 1), _row("CLEAN2", 2), _row("CLEAN3", 3),
                    _row("NEWGUY", 4)]
            b = _board(rows, [_rk("CLEAN1", 1, 1), _rk("CLEAN2", 2, 2),
                              _rk("NEWGUY", 3, 4), _rk("CLEAN3", 4, 3)])
            errs = validate_ranking.validate(b, archive_dir=d)
            self.assertTrue(any("DEGENERATE" in e and "top-3" in e for e in errs),
                            "non-gate top-3 churn must STILL trip the tripwire")


class GateRepairTests(unittest.TestCase):
    """AMEND-3 (commission 53): REPAIR not REJECT. A repairable breach (FAIL in top-3,
    correlated cluster all-top-3) is deterministically fixed and the board STILL
    publishes (fresh); only UNREPAIRABLE failures keep last-good. validate() still
    DETECTS the raw breach; repair() resolves it; the repaired board re-validates clean."""

    def _sorted(self, board):
        return sorted(board["kairos_ranking"], key=lambda r: r["kairos_rank"])

    def _states(self, board):
        return {r["ticker"]: ((r.get("detail") or {}).get("e") or {}).get("entry_state")
                for r in board["rows"]}

    # ── FAIL at #2 gets repaired: demoted, top-3 gate-clean, board re-validates ──
    def test_fail_at_rank2_repaired_and_publishes(self):
        rows = [_row("CLEAN1", 1), _row("KNIFE", 2, entry_state="FAIL"),
                _row("CLEAN2", 3), _row("CLEAN3", 4)]
        b = _board(rows, [_rk("CLEAN1", 1, 1), _rk("KNIFE", 2, 2),
                          _rk("CLEAN2", 3, 3), _rk("CLEAN3", 4, 4)])
        # raw board: validate() DETECTS the breach
        self.assertTrue(any("ENTRY-TRIGGER GATE breach" in e
                            for e in validate_ranking.validate(b, archive_dir="/nonexistent")))
        # repair it
        rep = validate_ranking.repair(b)
        self.assertTrue(rep["gate_repaired"])
        # KNIFE no longer in top-3; the next non-FAIL name pulled up
        top3 = {r["ticker"] for r in self._sorted(b)[:3]}
        self.assertNotIn("KNIFE", top3)
        self.assertEqual(top3, {"CLEAN1", "CLEAN2", "CLEAN3"})
        # KNIFE flagged transparently
        knife = next(r for r in b["kairos_ranking"] if r["ticker"] == "KNIFE")
        self.assertIn("gate-demoted", knife["gate_flag"])
        # repaired board re-validates with NO gate breach (idempotency / publishable)
        post = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertFalse(any("ENTRY-TRIGGER GATE breach" in e for e in post), post)
        # contiguous ranks preserved
        self.assertEqual([r["kairos_rank"] for r in self._sorted(b)], [1, 2, 3, 4])

    def test_fail_demoted_below_last_eligible(self):
        # KNIFE must land just AFTER the last PASS/SOFT name, not merely at #4.
        rows = [_row("KNIFE", 1, entry_state="FAIL"), _row("PASS1", 2),
                _row("SOFT1", 3, entry_state="SOFT"), _row("PASS2", 4)]
        b = _board(rows, [_rk("KNIFE", 1, 1), _rk("PASS1", 2, 2),
                          _rk("SOFT1", 3, 3), _rk("PASS2", 4, 4)])
        validate_ranking.repair(b)
        order = [r["ticker"] for r in self._sorted(b)]
        # all gate-eligible names ahead of the demoted knife
        self.assertLess(order.index("PASS2"), order.index("KNIFE"))
        self.assertNotIn("KNIFE", set(order[:3]))

    # ── correlation: cluster all-top-3 repaired (keep highest conviction) ────────
    def test_correlated_cluster_all_top3_repaired(self):
        rows = [_row("ZACOR", 1), _row("ZADYN", 2), _row("ZARNA", 3), _row("OTHER", 4)]
        rk = [_rk("ZACOR", 1, 1), _rk("ZADYN", 2, 2), _rk("ZARNA", 3, 3), _rk("OTHER", 4, 4)]
        # distinct convictions so the demotion target is deterministic
        rk[0]["conviction"] = "HIGH"
        rk[1]["conviction"] = "MODERATE"
        rk[2]["conviction"] = "LOW"
        b = _board(rows, rk)
        b["kairos_cluster_warnings"] = ["ZACOR + ZADYN + ZARNA all orbital_compute — one bet"]
        # validate() DETECTS
        self.assertTrue(any("CORRELATION ENFORCEMENT breach" in e
                            for e in validate_ranking.validate(b, archive_dir="/nonexistent")))
        rep = validate_ranking.repair(b)
        self.assertTrue(rep["correlation_repaired"])
        top3 = {r["ticker"] for r in self._sorted(b)[:3]}
        # highest-conviction member kept; not all three remain in top-3
        self.assertIn("ZACOR", top3)
        self.assertFalse({"ZACOR", "ZADYN", "ZARNA"} <= top3)
        # the lowest-conviction member (ZARNA) is the one demoted + flagged
        zarna = next(r for r in b["kairos_ranking"] if r["ticker"] == "ZARNA")
        self.assertIn("correlation-demoted", zarna["gate_flag"])
        post = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertFalse(any("CORRELATION ENFORCEMENT breach" in e for e in post), post)

    # ── idempotency: repairing an already-clean board is a no-op ────────────────
    def test_repair_idempotent(self):
        rows = [_row("CLEAN1", 1), _row("KNIFE", 2, entry_state="FAIL"),
                _row("CLEAN2", 3), _row("CLEAN3", 4)]
        b = _board(rows, [_rk("CLEAN1", 1, 1), _rk("KNIFE", 2, 2),
                          _rk("CLEAN2", 3, 3), _rk("CLEAN3", 4, 4)])
        validate_ranking.repair(b)
        order1 = [r["ticker"] for r in self._sorted(b)]
        rep2 = validate_ranking.repair(b)            # second pass
        order2 = [r["ticker"] for r in self._sorted(b)]
        self.assertEqual(order1, order2, "repair must be idempotent")
        self.assertFalse(rep2["gate_repaired"], "no-op second pass moves nothing")

    # ── flags surface on the board for the viewer ───────────────────────────────
    def test_repair_flags_surface(self):
        rows = [_row("KNIFE", 1, entry_state="FAIL"), _row("CLEAN", 2), _row("CLEAN2", 3)]
        b = _board(rows, [_rk("KNIFE", 1, 1), _rk("CLEAN", 2, 2), _rk("CLEAN2", 3, 3)])
        rep = validate_ranking.repair(b)
        self.assertIn("KNIFE", rep["moved"])
        flagged = [r for r in b["kairos_ranking"] if r.get("gate_flag")]
        self.assertTrue(flagged)
        self.assertTrue(all(isinstance(r["gate_flag"], str) and r["gate_flag"]
                            for r in flagged))

    # ── unrepairable still REJECTs (main() keeps last good) ─────────────────────
    def test_unrepairable_malformed_still_rejects(self):
        # invented ticker = NOT deterministically reorder-fixable → unrepairable
        rows = [_row("CLEAN1", 1), _row("CLEAN2", 2)]
        b = _board(rows, [_rk("GHOST", 1, 1), _rk("CLEAN1", 2, 1), _rk("CLEAN2", 3, 2)])
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("not in the universe" in e for e in errs))
        self.assertFalse(validate_ranking._is_repairable(errs),
                         "an invented name must be classed UNREPAIRABLE → fail-closed")

    def test_cannot_load_board_rejects(self):
        rc = validate_ranking.main(["validate_ranking", "/nonexistent/board.json"])
        self.assertEqual(rc, 1, "a board that cannot be loaded must REJECT (keep last good)")

    def test_gate_breach_is_classed_repairable(self):
        rows = [_row("KNIFE", 1, entry_state="FAIL"), _row("CLEAN", 2), _row("CLEAN2", 3)]
        b = _board(rows, [_rk("KNIFE", 1, 1), _rk("CLEAN", 2, 2), _rk("CLEAN2", 3, 3)])
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(validate_ranking._is_repairable(errs),
                        "a pure gate breach must be classed REPAIRABLE")

    # ── end-to-end via main(): a FAIL-at-top-3 board PUBLISHES (exit 0) fresh ────
    def test_main_repairs_and_publishes_exit0(self):
        rows = [_row("CLEAN1", 1), _row("KNIFE", 2, entry_state="FAIL"),
                _row("CLEAN2", 3), _row("CLEAN3", 4)]
        b = _board(rows, [_rk("CLEAN1", 1, 1), _rk("KNIFE", 2, 2),
                          _rk("CLEAN2", 3, 3), _rk("CLEAN3", 4, 4)])
        with tempfile.TemporaryDirectory() as d:
            bp = os.path.join(d, "board.json")
            with open(bp, "w") as f:
                json.dump(b, f)
            rc = validate_ranking.main(["validate_ranking", bp, "/nonexistent"])
            self.assertEqual(rc, 0, "repairable board must PUBLISH (exit 0), not freeze")
            # the repaired board was written back, gate-clean
            with open(bp) as f:
                out = json.load(f)
            top3 = {r["ticker"] for r in sorted(out["kairos_ranking"],
                                                key=lambda r: r["kairos_rank"])[:3]}
            self.assertNotIn("KNIFE", top3)
            knife = next(r for r in out["kairos_ranking"] if r["ticker"] == "KNIFE")
            self.assertIn("gate-demoted", knife["gate_flag"])


class BoardValidatorGateFieldTests(unittest.TestCase):
    def test_board_validator_requires_entry_state(self):
        # a row missing entry_state in detail.e must REJECT (fail-closed)
        rows = [_row("A", 1)]
        rows[0]["detail"]["e"].pop("entry_state")
        board = {"generated_at": "2026-06-19T00:00:00Z", "universe_size": 1,
                 "scored": 1, "unavailable": [], "rows": rows}
        errs = validate_board.validate(board)
        self.assertTrue(any("entry_state" in e for e in errs), errs)


# ════════════════════════════════════════════════════════════════════════════
# B. ENTRY/EXIT LEVELS
# ════════════════════════════════════════════════════════════════════════════
class EntryLevelsTests(unittest.TestCase):
    def _chart(self, n=120, base=100.0):
        close = [base + (i % 7) - 3 for i in range(n)]
        return {"price": close[-1], "close": close,
                "high": [c + 2 for c in close], "low": [c - 2 for c in close],
                "high_52w": max(close) + 20}

    def test_levels_basic_shape(self):
        lv = entry_levels.compute_levels(self._chart())
        self.assertIsNotNone(lv)
        self.assertIn("entry_zone", lv)
        self.assertEqual(len(lv["entry_zone"]), 2)
        self.assertLess(lv["stop"], lv["entry_zone"][0])  # stop below the zone
        self.assertIn(lv["levels_conviction"], ("HIGH", "MODERATE", "LOW"))

    def test_atr_null_bar_guard(self):
        # inject None bars — ATR must skip them, not crash/poison the mean.
        ch = self._chart()
        ch["high"][50] = None
        ch["low"][51] = None
        ch["close"][52] = None
        a = entry_levels.atr(ch)
        self.assertIsNotNone(a)
        self.assertTrue(a > 0)

    def test_too_few_clean_bars_suppresses(self):
        ch = {"price": 100, "close": [100, 101, 102],
              "high": [102, 103, 104], "low": [98, 99, 100], "high_52w": 110}
        self.assertIsNone(entry_levels.compute_levels(ch))  # < MIN_CLEAN_BARS

    def test_single_stop_anchor_monotone(self):
        # stop = recent_low - 1*ATR; must be a single, defined value below recent low.
        ch = self._chart()
        rl = entry_levels._recent_low(ch)
        a = entry_levels.atr(ch)
        lv = entry_levels.compute_levels(ch)
        self.assertAlmostEqual(lv["stop"], round(rl - a, 2), places=1)

    def test_missed_entry_guard_flag(self):
        # price far above the zone → "entry mostly left" flag
        ch = self._chart()
        ch["price"] = max(ch["close"]) + 50
        lv = entry_levels.compute_levels(ch)
        self.assertTrue(any("entry mostly left" in f for f in lv["flags"]), lv["flags"])

    def test_wild_atr_low_conviction(self):
        close = [100 + ((-1) ** i) * 40 for i in range(120)]  # huge swings
        ch = {"price": close[-1], "close": close,
              "high": [c + 30 for c in close], "low": [c - 30 for c in close],
              "high_52w": max(close)}
        lv = entry_levels.compute_levels(ch)
        self.assertEqual(lv["levels_conviction"], "LOW")

    def test_selector_caps_at_seven(self):
        rows = [{"ticker": f"T{i}", "mechanical_rank": i} for i in range(1, 20)]
        sel = entry_levels.select_level_names(rows, None, {"T9", "T10"}, cap=7)
        self.assertLessEqual(len(sel), 7)
        self.assertIn("T1", sel)            # top-5 present
        self.assertIn("T9", sel)            # just-became union present


# ════════════════════════════════════════════════════════════════════════════
# C. SYNTHETIC HARNESS
# ════════════════════════════════════════════════════════════════════════════
class SynthHarnessTests(unittest.TestCase):
    def test_dry_run_builds_all_scenarios(self):
        v = synth_validate.run_suite(dry_run=True)
        # every scenario built + invariants asserted, no Opus spend
        self.assertTrue(v.scenarios)
        self.assertTrue(all(s.get("built") for s in v.scenarios))
        self.assertEqual(len(v.scenarios), 12)  # the frozen 12 archetypes

    def test_generator_pool_is_decorrelated(self):
        # AMEND-3: the generator's large random pool must decorrelate (this is where
        # fingerprint-freedom actually lives). None = invariant holds.
        self.assertIsNone(synth_validate.gen_pool_decorrelated())

    def test_decorrelation_invariant_asserted(self):
        # a ≥12-name perfectly-correlated pool must be CAUGHT by _assert_decorrelated.
        rows = []
        for i in range(14):
            v = i / 14.0
            rows.append({"_spec_tags": [], "blocks": {
                "macro": v, "fundamental": v, "technical": v,
                "catalyst": v, "survival_gate": 0.3 + 0.7 * v}})
        err = synth_validate._assert_decorrelated(rows)
        self.assertIsNotNone(err)
        self.assertIn("decorrelation invariant breached", err)

    def test_collision_check_rerolls_or_raises(self):
        # a scenario pinning a REAL ticker must be caught as a collision.
        sc = {"id": "COLLIDE", "archetype": "x", "seed": 2, "filler": 0, "names": [
            {"ticker": "NVDA", "fundamental": 0.6, "technical": 0.3, "catalyst": 0.5,
             "survival_gate": 0.8, "themed": False},
            {"fundamental": 0.4, "technical": 0.6, "catalyst": 0.2,
             "survival_gate": 0.9, "themed": False},
        ]}
        with self.assertRaises(AssertionError):
            synth_validate.build_universe(sc)

    def test_real_composite_anchor(self):
        # mechanical_score must be the REAL es composite, not hand-set.
        sc = {"id": "ANCHOR", "archetype": "x", "seed": 3, "filler": 0, "names": [
            {"ticker": "ZZTEST", "fundamental": 0.5, "technical": 0.5, "catalyst": 0.5,
             "macro": 0.5, "survival_gate": 0.8, "themed": True, "theme_idx": 0}]}
        rows = synth_validate.build_universe(sc)
        import math as _m
        from . import equity_score as _es
        b = rows[0]["blocks"]
        expected = round(100.0 * b["survival_gate"] * (
            (0.30 * b["macro"] + 0.25 * b["fundamental"] + 0.25 * b["technical"]
             + 0.20 * b["catalyst"])), 1)
        self.assertTrue(_m.isclose(rows[0]["score"], expected, abs_tol=0.2))


class BoundaryGuardTests(unittest.TestCase):
    """The hard-coded boundary: synthetic output can NEVER be a profitability claim."""

    def test_bounded_verdict_refuses_profit_fields(self):
        v = synth_validate.BoundedVerdict()
        for bad in ("return", "pnl", "profit_pct", "alpha", "realised_gain",
                    "performance", "money_made"):
            with self.assertRaises(AttributeError, msg=f"{bad} must be refused"):
                setattr(v, bad, 1.23)

    def test_verdict_dict_carries_caveat(self):
        v = synth_validate.BoundedVerdict()
        d = v.to_dict()
        self.assertIn("BOUNDARY", d)
        self.assertIn("judgment", d["BOUNDARY"].lower())
        # the caveat itself names what it does NOT measure; the structural guard is
        # on field NAMES (tested above). Here we assert no DATA key looks like a PnL
        # field — i.e. the result keys carry judgment fields only.
        data_keys = {k.lower() for k in d if k != "BOUNDARY"}
        for bad in ("return", "pnl", "profit", "alpha", "gain", "performance"):
            self.assertFalse(any(bad in k for k in data_keys),
                             f"result key resembling '{bad}' leaked: {data_keys}")

    def test_judgment_fields_allowed(self):
        v = synth_validate.BoundedVerdict()
        v.verdict = "PASS"            # allowed
        v.robust_pass_rate = 0.9      # allowed
        self.assertEqual(v.verdict, "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
