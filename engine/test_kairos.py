"""
test_kairos.py — unit tests for the analyst-in-the-loop pipeline (stdlib unittest).

No live key required: the Opus ranking pass is exercised with a RECORDED mock
client response. Tests prove:
  - entry-timing E1/E2/E3 math,
  - kairos_rank bounding (gated-out names cannot be promoted),
  - the SANITISED exception path never echoes the key,
  - validate_ranking REJECTs invented names, gated promotions, unargued big moves,
  - the DEGENERATE tripwire (4-of-10 churn / top-3 change),
  - the key-safety final assertion.

Run:  python -m engine.test_kairos
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from . import entry_timing
from . import kairos_rank
from . import validate_ranking


# ── a recorded Anthropic-style response + fake client ────────────────────────
class _FakeBlock:
    type = "text"
    def __init__(self, text): self.text = text


class _FakeResp:
    model = "claude-opus-4-8"
    def __init__(self, payload): self.content = [_FakeBlock(json.dumps(payload))]


class _FakeMessages:
    def __init__(self, payload, capture): self._payload = payload; self._cap = capture
    def create(self, **kw):
        self._cap.update(kw)
        return _FakeResp(self._payload)


class _FakeClient:
    def __init__(self, payload, capture=None):
        self.messages = _FakeMessages(payload, capture if capture is not None else {})


def _mini_board():
    """Two rankable themed names + one gated-out (survival floor)."""
    def row(t, rank, score, gate, theme="T1", e=None):
        return {
            "ticker": t, "available": True, "score": score, "theme": theme,
            "theme_name": "Nuclear & AI-power", "macro": 82, "macro_stale": False,
            "themed": True, "price": 100.0,
            "blocks": {"macro": 0.82, "fundamental": 0.5, "technical": 0.3,
                       "catalyst": 0.4, "survival_gate": gate},
            "fundamental_branch": "standard", "fundamental_valid": True,
            "fundamental_flag": None,
            "mechanical_rank": rank, "mechanical_score": score,
            "detail": {"f": {}, "t": {}, "c": {"form4_buys_90d": 0},
                       "q": {}, "e": e or entry_timing.entry_block(None, None)},
        }
    return {
        "generated_at": "2026-06-18T22:00:00Z",
        "universe_size": 3, "scored": 3, "unavailable": [], "weights": {},
        "rows": [
            row("NVTS", 1, 48.6, 0.96),
            row("LEU", 2, 47.0, 0.90),
            row("FRAGILE", 3, 30.0, 0.30),  # survival floor → gated out
        ],
    }


class EntryTimingTests(unittest.TestCase):
    def test_e1_at_floor(self):
        # flat-then-current-at-low: price sitting on the recent low → retrace ~0
        chart = {"close": [100, 90, 80, 70, 60], "price": 60}
        self.assertAlmostEqual(entry_timing.retrace_off_low(chart), 0.0, places=2)

    def test_e1_fully_bounced(self):
        # bottomed at 50, price now AT the bounce high (90) → entry fully spent (1.0)
        chart = {"close": [100, 70, 50, 70, 90], "price": 90}
        self.assertAlmostEqual(entry_timing.retrace_off_low(chart), 1.0, places=2)

    def test_e1_mid_bounce(self):
        # bottomed at 50, ran to 90, pulled back to 70 → 70 of the 50->90 (40) move
        # = (70-50)/(90-50) = 0.5 spent (half the bounce given back)
        chart = {"close": [100, 70, 50, 90, 70], "price": 70}
        v = entry_timing.retrace_off_low(chart)
        self.assertAlmostEqual(v, 0.5, places=2)

    def test_e3_states(self):
        knife = {"close": [100 - i for i in range(25)], "price": 75}
        self.assertEqual(entry_timing.dislocation_state(knife), "knife")
        recovering = {"close": [50 + i for i in range(25)], "price": 74}
        self.assertEqual(entry_timing.dislocation_state(recovering), "recovering")
        basing = {"close": [50] * 25, "price": 50}
        self.assertEqual(entry_timing.dislocation_state(basing), "basing")

    def test_e2_honest_est_flag(self):
        subs = {"filings": {"recent": {"form": ["10-Q"], "filingDate": ["2026-05-01"]}}}
        e = entry_timing.catalyst_proximity(subs)
        self.assertEqual(e["catalyst_kind"], "filing_est")
        self.assertTrue(e["est"])  # honestly flagged as an estimate
        self.assertIsInstance(e["catalyst_days"], int)

    def test_e2_dated_wins(self):
        e = entry_timing.catalyst_proximity({}, watchlist_dated="2026-07-01")
        self.assertEqual(e["catalyst_kind"], "wl_dated")
        self.assertFalse(e["est"])


class KairosRankTests(unittest.TestCase):
    def test_split_excludes_gated(self):
        rankable, gated = kairos_rank.split_universe(_mini_board()["rows"])
        self.assertEqual({r["ticker"] for r in rankable}, {"NVTS", "LEU"})
        self.assertEqual({r["ticker"] for r in gated}, {"FRAGILE"})

    def test_prompt_omits_gated_from_rankable(self):
        _system, msgs = kairos_rank.build_messages(_mini_board())
        body = msgs[0]["content"]
        # FRAGILE appears only in the gated section, never as a rankable record
        self.assertIn("FRAGILE", body)
        # the rankable JSON block must not contain FRAGILE before the gated block
        rankable_part = body.split("GATED-OUT")[0]
        self.assertNotIn("FRAGILE", rankable_part)

    def test_rank_with_recorded_response(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-TESTKEY-do-not-log"
        payload = {
            "ranking": [
                {"ticker": "NVTS", "kairos_rank": 1, "mechanical_rank": 1, "delta": 0,
                 "conviction": "MODERATE-HIGH", "prob_tier": "~60%",
                 "rationale": "cleanest convexity; E1 still cheap", "correlation_note": ""},
                {"ticker": "LEU", "kairos_rank": 2, "mechanical_rank": 2, "delta": 0,
                 "conviction": "MODERATE", "prob_tier": "~50%",
                 "rationale": "good name but bounce spent (E1 high)", "correlation_note": "AI-power cluster w/ NVTS"},
            ],
            "gated_out": [{"ticker": "FRAGILE", "reason": "survival 0.30 — dilution"}],
            "cluster_warnings": ["NVTS+LEU both AI-power — size as one sleeve"],
        }
        out = kairos_rank.rank(_mini_board(), client=_FakeClient(payload))
        self.assertEqual(out["model"], "claude-opus-4-8")
        self.assertEqual(len(out["ranking"]), 2)
        self.assertEqual(out["ranking"][0]["ticker"], "NVTS")

    def test_low_temp_no_sampling_params(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-TESTKEY"
        cap = {}
        kairos_rank.rank(_mini_board(), client=_FakeClient({"ranking": [], "gated_out": [], "cluster_warnings": []}, cap))
        self.assertEqual(cap["model"], "claude-opus-4-8")
        self.assertNotIn("temperature", cap)  # Opus 4.8 rejects sampling params
        self.assertEqual(cap["output_config"]["effort"], "low")
        self.assertEqual(cap["output_config"]["format"]["type"], "json_schema")

    def test_key_absent_fail_closed(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with self.assertRaises(kairos_rank.KairosKeyError):
            kairos_rank.rank(_mini_board(), client=_FakeClient({}))

    def test_exception_path_sanitised(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-SECRET-LEAK-TEST"
        class _Boom:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("auth failed for key sk-ant-SECRET-LEAK-TEST in request")
            messages = messages()
        try:
            kairos_rank.rank(_mini_board(), client=_Boom())
            self.fail("expected KairosRankError")
        except kairos_rank.KairosRankError as e:
            self.assertNotIn("sk-ant-SECRET-LEAK-TEST", str(e))
            self.assertIn("REDACTED", str(e))

    def test_key_never_in_output(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-XYZ"
        # response that (maliciously) echoes the key → must be refused
        payload = {"ranking": [{"ticker": "NVTS", "kairos_rank": 1, "mechanical_rank": 1,
                                "delta": 0, "conviction": "HIGH", "prob_tier": "~60%",
                                "rationale": "leak sk-ant-XYZ", "correlation_note": ""}],
                   "gated_out": [], "cluster_warnings": []}
        with self.assertRaises(kairos_rank.KairosRankError):
            kairos_rank.rank(_mini_board(), client=_FakeClient(payload))


class ValidateRankingTests(unittest.TestCase):
    def _board_with_ranking(self, ranking, gated=None, clusters=None):
        b = _mini_board()
        b["kairos_ranking"] = ranking
        b["kairos_gated_out"] = gated or [{"ticker": "FRAGILE", "reason": "survival"}]
        b["kairos_cluster_warnings"] = clusters or []
        b["kairos_generated_at"] = "2026-06-18T22:05:00Z"
        return b

    def test_valid_passes(self):
        rk = [
            {"ticker": "NVTS", "kairos_rank": 1, "mechanical_rank": 1, "delta": 0,
             "conviction": "HIGH", "prob_tier": "~60%", "rationale": "cheap E1", "correlation_note": ""},
            {"ticker": "LEU", "kairos_rank": 2, "mechanical_rank": 2, "delta": 0,
             "conviction": "MODERATE", "prob_tier": "~50%", "rationale": "bounce spent", "correlation_note": ""},
        ]
        errs = validate_ranking.validate(self._board_with_ranking(rk), archive_dir="/nonexistent")
        self.assertEqual(errs, [], errs)

    def test_rejects_invented_name(self):
        rk = [{"ticker": "GHOST", "kairos_rank": 1, "mechanical_rank": 1, "delta": 0,
               "conviction": "HIGH", "prob_tier": "~60%", "rationale": "x", "correlation_note": ""}]
        errs = validate_ranking.validate(self._board_with_ranking(rk), archive_dir="/nonexistent")
        self.assertTrue(any("not in the universe" in e for e in errs), errs)

    def test_rejects_gated_promotion(self):
        rk = [{"ticker": "FRAGILE", "kairos_rank": 1, "mechanical_rank": 3, "delta": 2,
               "conviction": "HIGH", "prob_tier": "~60%",
               "rationale": "survival concern overridden", "correlation_note": ""}]
        errs = validate_ranking.validate(self._board_with_ranking(rk), archive_dir="/nonexistent")
        self.assertTrue(any("survival-gated name in ranking" in e for e in errs), errs)

    def test_rejects_unargued_big_move(self):
        # build a 12-name board so a 11-place move is possible
        b = _mini_board()
        rows = b["rows"][:2]
        for i in range(3, 14):
            r = dict(rows[0]); r = json.loads(json.dumps(r))
            r["ticker"] = f"N{i}"; r["mechanical_rank"] = i; r["blocks"]["survival_gate"] = 0.9
            rows.append(r)
        b["rows"] = rows
        b["kairos_gated_out"] = []; b["kairos_cluster_warnings"] = []
        b["kairos_generated_at"] = "2026-06-18T22:05:00Z"
        # N13 (mech 13) jumps to kairos_rank 1 = 12 places, rationale with NO factor token
        b["kairos_ranking"] = [{"ticker": "N13", "kairos_rank": 1, "mechanical_rank": 13,
                                "delta": 12, "conviction": "HIGH", "prob_tier": "~60%",
                                "rationale": "I just like it", "correlation_note": ""}]
        errs = validate_ranking.validate(b, archive_dir="/nonexistent")
        self.assertTrue(any("BIG-MOVE breach" in e for e in errs), errs)

    def test_degenerate_top3_change(self):
        with tempfile.TemporaryDirectory() as d:
            # seed a prior archive (NOT today) with a different top-3
            prior = {"ranking": [
                {"ticker": "LEU", "kairos_rank": 1}, {"ticker": "NVTS", "kairos_rank": 2},
                {"ticker": "X3", "kairos_rank": 3}]}
            with open(os.path.join(d, "2026-06-01.json"), "w") as f:
                json.dump(prior, f)
            rk = [
                {"ticker": "NVTS", "kairos_rank": 1, "mechanical_rank": 1, "delta": 0,
                 "conviction": "HIGH", "prob_tier": "~60%", "rationale": "cheap E1", "correlation_note": ""},
                {"ticker": "LEU", "kairos_rank": 2, "mechanical_rank": 2, "delta": 0,
                 "conviction": "MODERATE", "prob_tier": "~50%", "rationale": "bounce", "correlation_note": ""},
            ]
            errs = validate_ranking.validate(self._board_with_ranking(rk), archive_dir=d)
            # top-3 set changed (X3 dropped, ordering flip) → degenerate
            self.assertTrue(any("DEGENERATE" in e and "top-3" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
