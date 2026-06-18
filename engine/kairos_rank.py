"""
kairos_rank.py — LAYER 2: the analyst-in-the-loop ranking pass.

ONE low-temperature Opus 4.8 call per refresh. It receives the full factor table
for ALL scored names (the deterministic Layer-1 evidence) plus the theme register
and any watchlist dated triggers, and returns a STRUCTURED top-20 in descending
capital-deployment priority — each with a one-line WHY, conviction tier, and
probability bucket — PLUS gated_out[] and cluster_warnings[].

Design: 29-KAIROS-ANALYST-IN-LOOP-DESIGN.md §2, with validator amends (31):
  (A) the API exception path is SANITISED — a crash/traceback can NEVER echo the
      key or the prompt. Errors are scrubbed before any log/output.
  (C) degenerate-churn tripwire = 4-of-10 names churned OR a top-3-specific change
      → handled in validate_ranking.py (this module just produces the ranking).

MODEL: claude-opus-4-8 (Kunal-confirmed). Pricing per the claude-api skill at
build (2026-06-18): Opus 4.8 = $5 / $25 per Mtok. ~8k in / ~2k out per refresh
≈ $0.04 + $0.05 ≈ $0.09/refresh; daily ≈ $2.7/mo. The $10/mo guard (a monthly
call-count check, $/call ≈ $0.09 ⇒ ~111 calls/mo cap) is the runaway circuit-breaker.

SECRET: ANTHROPIC_API_KEY is read from os.environ ONLY. HARD-FAILS (fail-closed) if
absent. The key string NEVER appears in any committed file, prompt, board.json,
log, or the append-only ranking archive — a scrub asserts its absence on every write.

HARD constraints: visibility-only, no order rail, no Argus-book import. The ONLY
network this file touches is the Anthropic Messages API. Bounded: it ranks WITHIN
the survival gate (a gated-out name can NOT be promoted) and every >10-place move
vs mechanical_rank carries a mandatory argued WHY (enforced in validate_ranking).
"""
from __future__ import annotations

import json
import os
import sys

MODEL = "claude-opus-4-8"
# The survival-gate floor: a name at the floor is "gated out — survival" and is
# excluded from the rankable set (design §2A bound 1). equity_score maps the gate
# to [0.3, 1.0]; 0.30 (+ epsilon) is the floor.
GATE_FLOOR = 0.30
GATE_FLOOR_EPS = 1e-6
# How many names Kairos ranks (descending capital-deployment priority).
TOP_N = 20
# Monthly call-count runaway guard ($10/mo ÷ ~$0.09/call ≈ 111).
MONTHLY_CALL_CAP = 111

# Conviction + probability vocab the model must use (closed sets → no drift).
CONVICTION_TIERS = ["HIGH", "MODERATE-HIGH", "MODERATE", "LOW-MODERATE", "LOW"]
PROB_TIERS = ["~70%+", "~60%", "~50%", "~40%", "~30%-"]


# ── secret handling (fail-closed) ───────────────────────────────────────────
class KairosKeyError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is absent. Fail-closed: the pass does not run."""


def _require_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise KairosKeyError(
            "ANTHROPIC_API_KEY is not set — Kairos pass cannot run (fail-closed). "
            "Provide it as a GitHub Actions encrypted secret; never commit it."
        )
    return key


def _scrub(text: str, key: str | None) -> str:
    """
    Remove the API key from any string before it is logged or surfaced. Belt-and-
    suspenders against an accidental echo (design §3C rule 4). Also redacts the
    common Anthropic key prefix shape in case a *different* key leaks into a trace.
    """
    if not text:
        return text
    out = text
    if key:
        out = out.replace(key, "***REDACTED-KEY***")
    # redact anything that looks like an Anthropic key token (sk-ant-...)
    import re
    out = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "***REDACTED-KEY***", out)
    return out


# ── prompt contract ─────────────────────────────────────────────────────────
SYSTEM = """You are Kairos, the ranking analyst for a personal equity-opportunity scoreboard.

A deterministic factor engine has already scored ~40 names on macro, fundamental,
technical-timing, catalyst, and a multiplicative survival gate, and produced a
mechanical rank. Your job is NOT to re-derive that score. Your job is to RE-RANK
within bounds, integrating exactly the things the formula structurally cannot see:
  - entry timing (E1 retrace_off_low: 0.0 = still at the floor/cheap, 1.0 = bounce spent),
  - catalyst proximity (E2 catalyst_days/bucket; "est" means a filing-cadence estimate,
    NOT a published date — treat estimated catalysts as soft),
  - dislocation momentum (E3 dislocation_state: knife=still falling, basing=flat off
    the low/deployable, recovering=rising off the low),
  - cross-name correlation (don't stack 3 of the same theme as if diversified),
  - value-trap risk.

HARD BOUNDS (you will be rejected if you break these):
  1. Rank ONLY names present in the supplied universe. Never invent a ticker.
  2. A name listed as gated_out (survival gate at the floor) CANNOT appear in your
     ranking. List it in gated_out with the reason instead.
  3. Anchor to mechanical_rank. Re-order only where a NAMED factor justifies it.
     Any name you move MORE THAN 10 PLACES from its mechanical_rank MUST carry an
     explicit factor citation in its rationale (e.g. "E1=0.05 still at floor",
     "knife — wait", "AI-power cluster — size as one bet").
  4. If you rank an un-themed screen name (macro 0.40) ABOVE a themed name, the
     rationale must say why.

Operate in probabilities, not false precision — use coarse buckets only.
Every rationale is ONE line and must reference at least one concrete factor value.
Produce a cluster_warnings list naming any correlated names that should be sized
as a single theme sleeve rather than counted as diversification."""

INSTRUCTION = """Rank the top {top_n} names in DESCENDING capital-deployment priority.

For EACH ranked name return:
  - ticker, kairos_rank (1..N), mechanical_rank, delta (mechanical_rank - kairos_rank),
  - conviction (one of: {conv}),
  - prob_tier (one of: {prob}),
  - rationale (ONE line, must cite at least one concrete factor value; if |delta|>10,
    the factor citation justifying the big move is MANDATORY),
  - correlation_note (string; "" if none).

Also return:
  - gated_out: the survival-gated names you were given, each {{ticker, reason}}.
  - cluster_warnings: list of strings naming correlated clusters.

Here is the universe. RANKABLE names (you may rank these):
{rankable}

GATED-OUT names (survival floor — you may NOT rank these; echo them in gated_out):
{gated}

THEME REGISTER (for correlation reasoning):
{themes}
"""


def _factor_record(r: dict) -> dict:
    """Compact, model-facing factor record for one name — the EVIDENCE, not a summary."""
    b = r.get("blocks", {})
    e = (r.get("detail", {}) or {}).get("e", {}) or {}
    return {
        "ticker": r["ticker"],
        "theme": r.get("theme"),
        "theme_name": r.get("theme_name"),
        "macro": b.get("macro"),
        "macro_stale": r.get("macro_stale", False),
        "themed": r.get("themed", False),
        "fundamental": b.get("fundamental"),
        "fundamental_branch": r.get("fundamental_branch"),
        "technical": b.get("technical"),
        "catalyst": b.get("catalyst"),
        "survival_gate": b.get("survival_gate"),
        "mechanical_rank": r.get("mechanical_rank"),
        "mechanical_score": r.get("mechanical_score"),
        "price": r.get("price"),
        # entry-timing (Layer-1 extension)
        "E1_retrace_off_low": e.get("retrace_off_low"),
        "E2_catalyst_days": e.get("catalyst_days"),
        "E2_catalyst_kind": e.get("catalyst_kind"),
        "E2_catalyst_bucket": e.get("catalyst_bucket"),
        "E2_est": e.get("catalyst_est"),
        "E3_dislocation_state": e.get("dislocation_state"),
    }


def split_universe(rows: list) -> tuple[list, list]:
    """Partition scored rows into (rankable, gated_out_floor)."""
    rankable, gated = [], []
    for r in rows:
        sg = (r.get("blocks", {}) or {}).get("survival_gate")
        if isinstance(sg, (int, float)) and sg <= GATE_FLOOR + GATE_FLOOR_EPS:
            gated.append(r)
        else:
            rankable.append(r)
    return rankable, gated


def theme_register(rows: list) -> dict:
    """{theme_id: {name, members[]}} so the model can reason about correlation."""
    reg: dict = {}
    for r in rows:
        tid = r.get("theme")
        if not tid:
            continue
        reg.setdefault(tid, {"name": r.get("theme_name"), "members": []})
        reg[tid]["members"].append(r["ticker"])
    return reg


def build_messages(board: dict) -> tuple[str, list]:
    """Return (system, messages) for the Anthropic call. Pure — no network, no key."""
    rows = board.get("rows", [])
    rankable, gated = split_universe(rows)
    rankable_recs = [_factor_record(r) for r in rankable]
    gated_recs = [{"ticker": r["ticker"], "survival_gate": (r.get("blocks") or {}).get("survival_gate")}
                  for r in gated]
    instruction = INSTRUCTION.format(
        top_n=min(TOP_N, len(rankable)),
        conv=" / ".join(CONVICTION_TIERS),
        prob=" / ".join(PROB_TIERS),
        rankable=json.dumps(rankable_recs, indent=1),
        gated=json.dumps(gated_recs, indent=1),
        themes=json.dumps(theme_register(rows), indent=1),
    )
    return SYSTEM, [{"role": "user", "content": instruction}]


# ── structured output schema (forced JSON, parse-fail → fail-closed) ─────────
RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "kairos_rank": {"type": "integer"},
                    "mechanical_rank": {"type": "integer"},
                    "delta": {"type": "integer"},
                    "conviction": {"type": "string", "enum": CONVICTION_TIERS},
                    "prob_tier": {"type": "string", "enum": PROB_TIERS},
                    "rationale": {"type": "string"},
                    "correlation_note": {"type": "string"},
                },
                "required": ["ticker", "kairos_rank", "mechanical_rank", "delta",
                             "conviction", "prob_tier", "rationale", "correlation_note"],
                "additionalProperties": False,
            },
        },
        "gated_out": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["ticker", "reason"],
                "additionalProperties": False,
            },
        },
        "cluster_warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ranking", "gated_out", "cluster_warnings"],
    "additionalProperties": False,
}


# ── the call (SANITISED exception path) ─────────────────────────────────────
def rank(board: dict, *, client=None) -> dict:
    """
    Run ONE Opus 4.8 ranking pass. Returns the parsed ranking dict (schema §2B):
      {model, generated_at, ranking[], gated_out[], cluster_warnings[]}

    Raises:
      KairosKeyError              — key absent (fail-closed; never runs unkeyed).
      KairosRankError             — API/parse failure, with a SCRUBBED message
                                     (never echoes the key or the prompt).

    `client` is injectable for testing; in production it is anthropic.Anthropic().
    """
    key = _require_key()
    system, messages = build_messages(board)

    try:
        if client is None:
            import anthropic  # the one new dependency; imported lazily
            client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            # Low-temperature analytical judgement (design §2C control 1). Opus 4.8
            # uses adaptive thinking; effort low keeps the single ranking pass tight
            # and the output deterministic-leaning. No sampling params (4.8 rejects them).
            thinking={"type": "disabled"},
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": RANKING_SCHEMA},
            },
            system=system,
            messages=messages,
        )
    except KairosKeyError:
        raise
    except BaseException as exc:  # noqa: BLE001 — SANITISE everything (amend A)
        # CRITICAL: scrub before constructing the error so neither the key, the
        # full prompt, nor a traceback containing them is ever surfaced/logged.
        safe = _scrub(f"{exc.__class__.__name__}: {exc}", key)
        raise KairosRankError(f"Kairos API call failed (sanitised): {safe}") from None

    # extract the JSON text block (output_config.format guarantees JSON in the first text)
    try:
        text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
        parsed = json.loads(text)
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        safe = _scrub(f"{exc.__class__.__name__}: {exc}", key)
        raise KairosRankError(f"Kairos response not parseable JSON (sanitised): {safe}") from None

    import datetime as _dt
    parsed["model"] = getattr(resp, "model", MODEL)
    parsed["generated_at"] = _dt.datetime.utcnow().isoformat() + "Z"
    # FINAL key-safety assertion: the serialised result must not contain the key.
    blob = json.dumps(parsed)
    if key and key in blob:
        raise KairosRankError("Kairos output contained the API key — refusing to emit.")
    return parsed


class KairosRankError(RuntimeError):
    """Raised on a SANITISED API/parse failure. Message never contains the key/prompt."""


# ── monthly runaway guard ───────────────────────────────────────────────────
def check_monthly_guard(counter_path: str) -> tuple[bool, int]:
    """
    $10/mo runaway circuit-breaker by call-count. Reads/writes a tiny JSON
    {month: 'YYYY-MM', calls: N}. Returns (allowed, calls_this_month). When the cap
    is hit, allowed=False and the caller fails closed WITHOUT spending. Resets at
    month rollover. Stdlib-only; the file is gitignored (local CI counter).
    """
    import datetime as _dt
    month = _dt.date.today().strftime("%Y-%m")
    state = {"month": month, "calls": 0}
    try:
        with open(counter_path) as f:
            prev = json.load(f)
        if prev.get("month") == month:
            state = prev
    except (OSError, ValueError):
        pass
    if state.get("month") != month:
        state = {"month": month, "calls": 0}
    if state["calls"] >= MONTHLY_CALL_CAP:
        return False, state["calls"]
    return True, state["calls"]


def bump_monthly_guard(counter_path: str) -> None:
    import datetime as _dt
    month = _dt.date.today().strftime("%Y-%m")
    state = {"month": month, "calls": 0}
    try:
        with open(counter_path) as f:
            prev = json.load(f)
        if prev.get("month") == month:
            state = prev
    except (OSError, ValueError):
        pass
    if state.get("month") != month:
        state = {"month": month, "calls": 0}
    state["calls"] += 1
    try:
        with open(counter_path, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


# ── CLI entrypoint: board.json (+ archive) → board.json with kairos_ranking[] ─
def main(argv) -> int:
    """
    Reads outputs/board.json, runs the Kairos pass, writes kairos_ranking[] +
    gated_out[] + cluster_warnings[] into the board, appends the raw response to
    the append-only archive, and writes the board back.

    Fail-closed: ANY failure (key absent / API down / parse fail / guard tripped)
    exits non-zero WITHOUT mutating board.json. The workflow then does not publish
    and the last-good board stays served.
    """
    import datetime as _dt
    board_path = argv[1] if len(argv) > 1 else "outputs/board.json"
    archive_dir = argv[2] if len(argv) > 2 else "outputs/kairos-rankings"
    counter_path = argv[3] if len(argv) > 3 else ".kairos_calls.json"

    try:
        with open(board_path) as f:
            board = json.load(f)
    except (OSError, ValueError) as e:
        print(f"REJECT — cannot load {board_path}: {e.__class__.__name__}", file=sys.stderr)
        return 1

    # monthly runaway guard (do NOT spend if tripped)
    allowed, calls = check_monthly_guard(counter_path)
    if not allowed:
        print(f"REJECT — monthly call cap reached ({calls} >= {MONTHLY_CALL_CAP}); "
              f"fail-closed, no spend, last-good board stays served.", file=sys.stderr)
        return 1

    try:
        ranking = rank(board)
        bump_monthly_guard(counter_path)
    except (KairosKeyError, KairosRankError) as e:
        # message is already sanitised; safe to print
        print(f"REJECT — Kairos pass failed: {e}", file=sys.stderr)
        return 1

    # write kairos fields onto the board object (Layer 2 output)
    board["kairos_ranking"] = ranking["ranking"]
    board["kairos_gated_out"] = ranking["gated_out"]
    board["kairos_cluster_warnings"] = ranking["cluster_warnings"]
    board["kairos_model"] = ranking["model"]
    board["kairos_generated_at"] = ranking["generated_at"]

    # append-only archive (the proof-loop dataset doubles as the audit log)
    os.makedirs(archive_dir, exist_ok=True)
    snap = {
        "generated_at": ranking["generated_at"],
        "model": ranking["model"],
        "ranking": ranking["ranking"],
        "gated_out": ranking["gated_out"],
        "cluster_warnings": ranking["cluster_warnings"],
        # snapshot price + mechanical rank per ranked name → forward-tracking dataset
        "snapshot": {r["ticker"]: {"price": r.get("price"),
                                   "mechanical_rank": r.get("mechanical_rank"),
                                   "mechanical_score": r.get("mechanical_score")}
                     for r in board.get("rows", [])},
    }
    day = _dt.date.today().isoformat()
    with open(os.path.join(archive_dir, f"{day}.json"), "w") as f:
        json.dump(snap, f, indent=2)

    with open(board_path, "w") as f:
        json.dump(board, f, indent=2)

    print(f"OK — Kairos ranked {len(ranking['ranking'])} names "
          f"({len(ranking['gated_out'])} gated out, "
          f"{len(ranking['cluster_warnings'])} cluster warnings); "
          f"archive {day}.json written; monthly calls={calls + 1}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
