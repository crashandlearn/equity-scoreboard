// Shape of board.json the engine (run_board.py) emits. Public-safe by construction:
// only signals + the deterministic composite score. No key, no broker, no order.

export interface ScoreBlocks {
  macro: number;
  fundamental: number | null;
  technical: number;
  catalyst: number;
  survival_gate: number;
}

// The rich 14-signal detail the engine emits per row (tap-to-expand surface).
export interface RowDetail {
  f: {
    rev_ttm: number | null;
    pre_revenue: boolean;
    components: Partial<Record<"F1" | "F2" | "F3" | "F4", number | null>>;
    branch: string;
    valid_signals?: string[];
    proxy?: number;
    // foreign/IFRS filers: no us-gaap XBRL → no computable fundamental
    flag?: string;
    neutral_prior?: number;
  };
  t: { drawdown: number; rvol: number; gap: number; rsi: number };
  c: { form4_buys_90d: number; filings8k_30d: number; wl_trigger: boolean };
  q: { runway_q: number | null; dilution: number };
  // Layer-1 entry-timing extension (E1/E2/E3 — entry_timing.py). Present once the
  // analyst-in-the-loop build ships; older boards omit it.
  e?: {
    retrace_off_low: number | null;
    catalyst_days: number | null;
    catalyst_kind: "wl_dated" | "filing_est" | "none";
    catalyst_bucket: "hot" | "warm" | "cold" | null;
    catalyst_est: boolean;
    dislocation_state: "knife" | "basing" | "recovering";
    // ENTRY-TRIGGER GATE (commission 44 A) — engine-side deterministic gate value.
    entry_trigger?: number;
    entry_state?: "PASS" | "SOFT" | "FAIL";
    structure_reclaim?: boolean | null;
  };
  // ENTRY/EXIT LEVELS (commission 44 B) — present only on the top-N rows that carry
  // a precomputed levels block (top-5 ∪ justBecameAttractive, cap ~7).
  levels?: EntryExitLevels;
}

// ENTRY/EXIT LEVELS panel data (entry_levels.py). Suggested ZONES, not precise calls.
export interface EntryExitLevels {
  current_price: number;
  atr: number;
  entry_zone: [number, number];
  target: number;
  stop: number;
  rr: number | null;
  distance_to_entry: number;
  levels_conviction: "HIGH" | "MODERATE" | "LOW";
  flags: string[];
  note: string;
}

// One row of the Layer-2 Kairos ranking (the analyst's pick — board.kairos_ranking[]).
export interface KairosRank {
  ticker: string;
  kairos_rank: number;
  mechanical_rank: number;
  delta: number;
  conviction: string;
  prob_tier: string;
  rationale: string;
  correlation_note: string;
  // ENTRY/EXIT LEVELS attached engine-side for the top-N (commission 44 B).
  levels?: EntryExitLevels;
}

export interface BoardRow {
  ticker: string;
  available: boolean;
  score: number;
  theme: string | null;
  theme_name: string | null;
  macro: number;
  macro_stale: boolean;
  themed: boolean;
  price: number;
  blocks: ScoreBlocks;
  fundamental_branch: string | null;
  fundamental_valid: boolean;
  // foreign/IFRS filers: "fundamental unavailable — foreign/IFRS filer (no us-gaap XBRL)"
  fundamental_flag?: string | null;
  detail?: RowDetail;
  // client-derived (not from engine):
  rank?: number;
  justBecameAttractive?: boolean;
  // Layer-2 Kairos pick, joined onto the row by the loader (client-derived from
  // board.kairos_ranking). Absent if the name isn't in Kairos's top-N or no
  // ranking shipped — falls back to the mechanical order. No black box: both
  // ranks + the WHY render side by side.
  mechanicalRank?: number;
  kairosRank?: number | null;
  kairosDelta?: number | null;
  kairosWhy?: string | null;
  kairosConviction?: string | null;
  kairosProbTier?: string | null;
  kairosCorrelation?: string | null;
  // ENTRY/EXIT LEVELS for the top-N (commission 44 B), joined from kairos_ranking.
  levels?: EntryExitLevels | null;
}

export interface Board {
  generated_at: string;
  universe_size: number;
  scored: number;
  unavailable: string[];
  weights: { macro: number; fund: number; tech: number; cat: number };
  rows: BoardRow[];
  // Layer-2 (analyst-in-the-loop) — optional; older boards omit these.
  kairos_ranking?: KairosRank[];
  kairos_gated_out?: { ticker: string; reason: string }[];
  kairos_cluster_warnings?: string[];
  kairos_model?: string;
  kairos_generated_at?: string;
}

export type BoardSource = "file" | "blob" | "unavailable";

export interface BoardUnavailable {
  reason: "no_source" | "miss" | "parse_error";
  detail: string;
}

// ── Theme register (mirror of data/themes.json) ─────────────────────────────
export interface ThemeDimensionRationale {
  structural: string;
  supply: string;
  winner: string;
  policy: string;
}

export interface ThemeMeta {
  id: string;
  name: string;
  short: string;
  macro: number;
  structural: number;
  supply: number;
  winner: number;
  policy: number;
  last_reviewed: string;
  seed_tickers: string[];
  rationale: ThemeDimensionRationale;
}
