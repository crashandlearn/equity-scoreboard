import type { Board, BoardSource, BoardUnavailable, BoardRow } from "./types";

// ── READ-ONLY board loader (static-export, client-side) ──────────────────────
// The viewer renders the static board.json the engine emits. It NEVER computes
// scores, NEVER connects to a broker, NEVER places an order. It is a pure render
// of a file the engine produced.
//
// Source: a static `board.json` co-located with the site (GitHub Pages serves it
// at `${basePath}/board.json`). The engine's job is to drop a fresh board.json
// into the published directory on its cadence; the page re-fetches it.
//
// HONESTY DOCTRINE (inherited from the Pit viewer): on miss / parse-error we
// return source:"unavailable" with board:null + a reason. We NEVER fabricate a
// board. No mock-fallback dressed as live state.

const STALE_AFTER_MS = 6 * 60 * 60 * 1000; // 6h: board is "stale" past this

export interface BoardResult {
  board: Board | null;
  source: BoardSource;
  unavailable: BoardUnavailable | null;
  stale: boolean;
}

// basePath-aware URL so it works at a repo subpath on GitHub Pages.
export function boardUrl(): string {
  const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
  return `${base}/board.json`;
}

export async function fetchBoard(): Promise<BoardResult> {
  let raw: string;
  try {
    const r = await fetch(boardUrl(), { cache: "no-store" });
    if (!r.ok) {
      return {
        board: null,
        source: "unavailable",
        stale: false,
        unavailable: { reason: "miss", detail: `board.json HTTP ${r.status}` },
      };
    }
    raw = await r.text();
  } catch {
    return {
      board: null,
      source: "unavailable",
      stale: false,
      unavailable: { reason: "miss", detail: "board.json unreachable" },
    };
  }

  let board: Board;
  try {
    board = JSON.parse(raw) as Board;
  } catch {
    return {
      board: null,
      source: "unavailable",
      stale: false,
      unavailable: { reason: "parse_error", detail: "board.json failed to parse" },
    };
  }

  return {
    board: decorate(board),
    source: "file",
    stale: isStale(board.generated_at),
    unavailable: null,
  };
}

function isStale(generatedAt: string): boolean {
  const t = Date.parse(generatedAt);
  if (Number.isNaN(t)) return true;
  return Date.now() - t > STALE_AFTER_MS;
}

// ── rank + JUST-BECAME-ATTRACTIVE decoration ────────────────────────────────
// The engine emits rows already sorted by score. We add 1-based rank and the
// transition flag. "JUST BECAME ATTRACTIVE" fires when (macro+fundamental)
// sub-score is top-quartile AND the technical-timing block printed a fresh
// dislocation (>= the dislocation threshold). This is a within-snapshot proxy
// for the design's transition rule; a cross-snapshot diff (rank entered top
// quintile since last refresh) is the engine's job once it persists history.
const DISLOCATION_THRESHOLD = 0.25;

function decorate(board: Board): Board {
  // Mechanical order = engine score order. This is the deterministic ANCHOR and
  // the fail-closed fallback ordering (a name's mechanical_rank from the engine,
  // or its 1-based score position if the engine field is absent).
  const byScore = [...board.rows].sort((a, b) => b.score - a.score);
  byScore.forEach((r, i) => {
    r.mechanicalRank = (r as unknown as { mechanical_rank?: number }).mechanical_rank ?? i + 1;
  });

  // Layer-2 join: map ticker → Kairos pick (rank / delta / WHY / conviction).
  const kmap = new Map(
    (board.kairos_ranking ?? []).map((k) => [k.ticker, k]),
  );
  byScore.forEach((r) => {
    const k = kmap.get(r.ticker);
    if (k) {
      r.kairosRank = k.kairos_rank;
      r.kairosDelta = k.delta;
      r.kairosWhy = k.rationale;
      r.kairosConviction = k.conviction;
      r.kairosProbTier = k.prob_tier;
      r.kairosCorrelation = k.correlation_note || null;
    } else {
      r.kairosRank = null;
      r.kairosDelta = null;
      r.kairosWhy = null;
      r.kairosConviction = null;
      r.kairosProbTier = null;
      r.kairosCorrelation = null;
    }
  });

  // Display order: when a Kairos ranking is present, it drives the board (the
  // analyst is the ranker). Kairos-ranked names come first in kairos_rank order;
  // un-ranked names (outside the top-N) trail in mechanical order. No ranking →
  // pure mechanical order (the fail-closed fallback). The visible `rank` badge is
  // the DISPLAY position; mechanicalRank/kairosRank render side by side on the card.
  const hasKairos = (board.kairos_ranking?.length ?? 0) > 0;
  const rows = hasKairos
    ? [...byScore].sort((a, b) => {
        const ak = a.kairosRank ?? Infinity;
        const bk = b.kairosRank ?? Infinity;
        if (ak !== bk) return ak - bk;
        return (a.mechanicalRank ?? 0) - (b.mechanicalRank ?? 0);
      })
    : byScore;

  const n = rows.length;
  const conviction = rows.map(convictionScore);
  const sortedConv = [...conviction].sort((a, b) => b - a);
  const q1Cut = sortedConv[Math.max(0, Math.ceil(n * 0.25) - 1)] ?? 1;

  rows.forEach((r, i) => {
    r.rank = i + 1;
    const conv = convictionScore(r);
    const freshDislocation = r.blocks.technical >= DISLOCATION_THRESHOLD;
    r.justBecameAttractive = conv >= q1Cut && freshDislocation;
  });
  return { ...board, rows };
}

export function convictionScore(r: BoardRow): number {
  const macro = r.macro / 100;
  const fund = r.blocks.fundamental ?? 0.4; // unknown fundamental → neutral
  // mirror the additive macro:fund ratio (0.30 : 0.25) from the engine weights
  return (0.3 * macro + 0.25 * fund) / 0.55;
}
