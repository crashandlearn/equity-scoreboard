"""
run_board.py — observe → score → render the STATIC ranked board (Phase 1).

Reads data/themes.json, fetches chart (approved structured path) + EDGAR facts +
submissions for every themed + screen ticker, computes per-theme cohort returns
(for F4 relative-strength), scores all names, ranks, writes:
  - outputs/board.json   (machine-readable, the viewer + acceptance test read this)
  - outputs/board.md     (human-readable ranked table)

Usage:
  python -m engine.run_board            # full universe
  python -m engine.run_board OKLO MP    # just these (acceptance spot-check)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

from . import observe
from . import equity_score as es
from . import entry_timing

HERE = os.path.dirname(__file__)
THEMES = os.path.join(HERE, "..", "data", "themes.json")
OUT = os.path.join(HERE, "..", "outputs")


def load_themes():
    with open(THEMES) as f:
        return json.load(f)


def _stale(last_reviewed: str, stale_after: int) -> bool:
    try:
        d = _dt.date.fromisoformat(last_reviewed)
        return (_dt.date.today() - d).days > stale_after
    except (ValueError, TypeError):
        return True


def build_universe(cfg, only=None):
    """Return list of (ticker, theme_dict_or_None, macro_stale)."""
    rows = []
    seen = set()
    for th in cfg["themes"]:
        stale = _stale(th["last_reviewed"], cfg.get("stale_after_days", 14))
        for tk in th["seed_tickers"]:
            if tk in seen:
                continue
            seen.add(tk)
            rows.append((tk, th, stale))
    for tk in cfg.get("screen_tranche", []):
        if tk in seen:
            continue
        seen.add(tk)
        rows.append((tk, None, False))
    if only:
        only = {t.upper() for t in only}
        rows = [r for r in rows if r[0].upper() in only]
    return rows


def observe_all(universe):
    """Fetch chart + facts + submissions for each name. Returns {ticker: obs}."""
    obs = {}
    for i, (tk, theme, stale) in enumerate(universe):
        chart = observe.fetch_chart(tk, rng="1y", interval="1d")
        facts = observe.fetch_companyfacts(tk)
        subs = observe.fetch_submissions(tk)
        obs[tk] = {
            "ticker": tk, "theme": theme, "macro_stale": stale,
            "chart": chart, "facts": facts, "submissions": subs,
            "watchlist_trigger": False,
        }
        print(f"  [{i+1}/{len(universe)}] {tk}: chart={'ok' if chart else 'MISS'} "
              f"facts={'ok' if facts else 'miss'}", file=sys.stderr)
    return obs


def cohort_returns(cfg, obs):
    """Per-theme list of 120d returns (for F4). Returns {theme_id: [returns]}."""
    out = {}
    for th in cfg["themes"]:
        rets = []
        for tk in th["seed_tickers"]:
            c = obs.get(tk, {}).get("chart")
            if c:
                r = es._trailing_return(c, 120)
                if r is not None:
                    rets.append(r)
        out[th["id"]] = rets
    return out


def run(only=None):
    cfg = load_themes()
    universe = build_universe(cfg, only)
    print(f"Universe: {len(universe)} names", file=sys.stderr)
    obs = observe_all(universe)
    cohorts = cohort_returns(cfg, obs)
    prior = cfg.get("neutral_macro_prior", 40)

    scored = []
    for tk, o in obs.items():
        coh = cohorts.get(o["theme"]["id"], []) if o["theme"] else []
        scored.append(es.score_name(o, coh, neutral_prior=prior))

    ranked = sorted(
        [s for s in scored if s.get("available")],
        key=lambda s: s["score"], reverse=True)
    unavailable = [s for s in scored if not s.get("available")]

    # ── Layer-1 EXTENSION: stamp mechanical rank + entry-timing block ─────────
    # mechanical_rank/score is the deterministic ANCHOR Layer 2 reasons against and
    # the fail-closed fallback ordering. detail.e carries E1/E2/E3 (entry_timing).
    # Neither mutates the composite — both are additive, leaving the v2 scorer pure.
    for i, r in enumerate(ranked, 1):
        r["mechanical_rank"] = i
        r["mechanical_score"] = r["score"]
        o = obs.get(r["ticker"], {})
        r["detail"]["e"] = entry_timing.entry_block(
            o.get("chart"), o.get("submissions"),
            watchlist_dated=o.get("watchlist_dated"),
        )

    board = {
        "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
        "universe_size": len(universe),
        "scored": len(ranked),
        "unavailable": [s["ticker"] for s in unavailable],
        "weights": es.W,
        "rows": ranked,
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "board.json"), "w") as f:
        json.dump(board, f, indent=2)
    _write_md(board)
    print(f"\nWrote {len(ranked)} ranked rows ({len(unavailable)} unavailable) to outputs/",
          file=sys.stderr)
    return board


def _write_md(board):
    lines = [
        "# Equity Opportunity Scoreboard — ranked (Phase 1, static)",
        f"_generated {board['generated_at']} · {board['scored']} scored / "
        f"{board['universe_size']} universe · visibility-only, no order rail_",
        "",
        "Weights: Macro 0.30 / Fundamental 0.25 / Technical 0.25 / Catalyst 0.20 × survival-gate.",
        "Ranks **setup attractiveness, not outcome.** F-branch shows how the fundamental sub-score was computed.",
        "",
        "| # | Ticker | Score | Theme | Macro | Fund | F-branch | Tech | Cat | Gate | Price |",
        "|---|--------|-------|-------|-------|------|----------|------|-----|------|-------|",
    ]
    for i, r in enumerate(board["rows"], 1):
        b = r["blocks"]
        macro_cell = f"{r['macro']}{' ⚠STALE' if r['macro_stale'] else ''}" + (
            "" if r["themed"] else " (un-themed)")
        fund = f"{b['fundamental']:.2f}" if b["fundamental"] is not None else "—"
        if r.get("fundamental_flag"):
            fund += " ⚠"  # foreign/IFRS filer — neutral prior, not a real fundamental
        fbranch = r["fundamental_branch"] or "—"
        lines.append(
            f"| {i} | **{r['ticker']}** | {r['score']} | {r['theme'] or '—'} | "
            f"{macro_cell} | {fund} | {fbranch} | "
            f"{b['technical']:.2f} | {b['catalyst']:.2f} | {b['survival_gate']:.2f} | "
            f"${r['price']} |")
    if board["unavailable"]:
        lines += ["", f"**Data-unavailable (chart MISS, not fabricated):** "
                  f"{', '.join(board['unavailable'])}"]
    with open(os.path.join(OUT, "board.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    run(only)
