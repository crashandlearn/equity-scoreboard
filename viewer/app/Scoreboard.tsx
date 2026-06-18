"use client";

import { useEffect, useMemo, useState } from "react";
import type { Board, BoardRow, BoardSource, BoardUnavailable, ThemeMeta } from "@/lib/types";
import { fetchBoard, convictionScore } from "@/lib/board";
import { THEMES, THEME_BY_ID, daysSince, STALE_AFTER_DAYS } from "@/lib/themes";

const REFRESH_MS = 90_000; // re-fetch board.json every 90s

// Block colours — one hue per asymmetry source, used everywhere it appears so
// the eye learns "purple = macro, teal = fundamental" across the whole board.
const BLOCK = {
  macro: { key: "macro", label: "Macro", c: "#a78bfa", w: 0.3 },
  fund: { key: "fundamental", label: "Fundamental", c: "#34d399", w: 0.25 },
  tech: { key: "technical", label: "Technical timing", c: "#38bdf8", w: 0.25 },
  cat: { key: "catalyst", label: "Catalyst", c: "#fbbf24", w: 0.2 },
} as const;

export default function Scoreboard() {
  const [board, setBoard] = useState<Board | null>(null);
  const [source, setSource] = useState<BoardSource>("unavailable");
  const [stale, setStale] = useState(false);
  const [unavailable, setUnavailable] = useState<BoardUnavailable | null>(null);
  const [loading, setLoading] = useState(true);

  // filters
  const [theme, setTheme] = useState<string>("");
  const [themedOnly, setThemedOnly] = useState(false);
  const [attractiveOnly, setAttractiveOnly] = useState(false);

  // ui
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showHow, setShowHow] = useState(false);
  const [showThemes, setShowThemes] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const r = await fetchBoard();
      if (!alive) return;
      setBoard(r.board);
      setSource(r.source);
      setStale(r.stale);
      setUnavailable(r.unavailable);
      setLoading(false);
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const rows = useMemo(() => {
    if (!board) return [];
    return board.rows.filter((r) => {
      if (themedOnly && !r.themed) return false;
      if (theme && r.theme !== theme) return false;
      if (attractiveOnly && !r.justBecameAttractive) return false;
      return true;
    });
  }, [board, theme, themedOnly, attractiveOnly]);

  const attractiveCount = board?.rows.filter((r) => r.justBecameAttractive).length ?? 0;
  const themesInBoard = useMemo(() => {
    if (!board) return [] as string[];
    return [...new Set(board.rows.map((r) => r.theme).filter(Boolean) as string[])];
  }, [board]);

  return (
    <div className="page">
      <Aurora />
      <main className="wrap">
        {/* ── Masthead ─────────────────────────────────────────────── */}
        <header className="mast">
          <div className="kicker">
            <span className="pulse" /> RAZOR · EQUITY OPPORTUNITY
          </div>
          <h1 className="title">
            The <em>asymmetry</em> scoreboard
          </h1>
          <p className="lede">
            A ranked read on <strong>where the setup is attractive today</strong> — each name
            scored on four independent sources of asymmetry, gated by survival.
          </p>

          <Freshness
            loading={loading}
            unavailable={unavailable}
            board={board}
            source={source}
            stale={stale}
          />

          {board?.kairos_ranking && board.kairos_ranking.length > 0 && (
            <div className="kairos-meta">
              <span className="km-tag">ANALYST-IN-THE-LOOP</span>
              <span className="km-text">
                ranking by <strong>Kairos</strong>
                {board.kairos_model ? ` · ${board.kairos_model}` : ""} — the board is the
                analyst&rsquo;s pick, shown beside the formula&rsquo;s. No black box.
              </span>
            </div>
          )}
        </header>

        {board?.kairos_cluster_warnings && board.kairos_cluster_warnings.length > 0 && (
          <div className="cluster-warn" role="note">
            <span className="cw-icon">⛓</span>
            <div>
              <strong>Correlation read</strong> — the formula structurally can&rsquo;t see this:
              <ul className="cw-list">
                {board.kairos_cluster_warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* ── Weight legend (how the score is composed) ───────────────── */}
        <section className="legend">
          {Object.values(BLOCK).map((b) => (
            <div className="legend-item" key={b.key}>
              <span className="legend-dot" style={{ background: b.c }} />
              <span className="legend-label">{b.label}</span>
              <span className="legend-w">{Math.round(b.w * 100)}%</span>
            </div>
          ))}
          <div className="legend-item gate">
            <span className="legend-dot gatedot" />
            <span className="legend-label">Survival gate</span>
            <span className="legend-w">×0.3–1.0</span>
          </div>
        </section>

        {/* ── Expandable "how this is ranked" ─────────────────────────── */}
        <button className="disclose" onClick={() => setShowHow((v) => !v)}>
          <span className="disclose-q">How is this ranked?</span>
          <span className={`chev ${showHow ? "open" : ""}`}>›</span>
        </button>
        {showHow && <HowRanked />}

        {/* ── Just-became-attractive banner ───────────────────────────── */}
        {attractiveCount > 0 && (
          <button
            className={`jba-banner ${attractiveOnly ? "active" : ""}`}
            onClick={() => setAttractiveOnly((v) => !v)}
          >
            <span className="jba-bolt">⚡</span>
            <span>
              <strong>{attractiveCount}</strong> name{attractiveCount > 1 ? "s" : ""} just became
              attractive
            </span>
            <span className="jba-sub">structurally-asymmetric &amp; freshly dislocated · tap to filter</span>
          </button>
        )}

        {/* ── Theme rail ──────────────────────────────────────────────── */}
        <button className="disclose" onClick={() => setShowThemes((v) => !v)}>
          <span className="disclose-q">
            The 6 themes &amp; their macro thesis <span className="muted-inline">— the oxygen factor</span>
          </span>
          <span className={`chev ${showThemes ? "open" : ""}`}>›</span>
        </button>
        {showThemes && <ThemeGrid active={theme} onPick={setTheme} />}

        {/* ── Filters ─────────────────────────────────────────────────── */}
        {board && (
          <div className="filters" role="tablist" aria-label="Filter by theme">
            <button
              className={`fchip ${theme === "" && !themedOnly ? "on" : ""}`}
              onClick={() => {
                setTheme("");
                setThemedOnly(false);
              }}
            >
              All
            </button>
            <button
              className={`fchip ${themedOnly ? "on" : ""}`}
              onClick={() => {
                setThemedOnly((v) => !v);
                setTheme("");
              }}
            >
              Themed
            </button>
            {THEMES.filter((t) => themesInBoard.includes(t.id)).map((t) => (
              <button
                key={t.id}
                className={`fchip ${theme === t.id ? "on" : ""}`}
                onClick={() => {
                  setTheme((cur) => (cur === t.id ? "" : t.id));
                  setThemedOnly(false);
                }}
                style={
                  theme === t.id
                    ? { borderColor: themeAccent(t.id), color: themeAccent(t.id) }
                    : undefined
                }
              >
                {t.short}
              </button>
            ))}
          </div>
        )}

        {/* ── The ranked board ────────────────────────────────────────── */}
        {board ? (
          rows.length === 0 ? (
            <div className="empty">No names match this filter.</div>
          ) : (
            <ol className="board">
              {rows.map((r) => (
                <NameCard
                  key={r.ticker}
                  r={r}
                  open={expanded === r.ticker}
                  onToggle={() =>
                    setExpanded((cur) => (cur === r.ticker ? null : r.ticker))
                  }
                />
              ))}
            </ol>
          )
        ) : !loading ? (
          <div className="empty unavail">
            DATA UNAVAILABLE — {unavailable?.detail ?? "no board"}. The board never invents
            numbers; on a source miss it shows nothing.
          </div>
        ) : (
          <div className="empty">Loading board…</div>
        )}

        {board && board.unavailable.length > 0 && (
          <div className="dataskip">
            Excluded (chart miss — not fabricated): {board.unavailable.join(", ")}
          </div>
        )}

        {/* ── Footer / wall ───────────────────────────────────────────── */}
        <footer className="footer">
          <p>
            <strong>Visibility only.</strong> Read-only board in the <code>rzrtrdr/razor</code>{" "}
            family — no broker connection, no order rail, no account data, no write path anywhere
            in this artefact. It renders the static board the scoring engine emits.
          </p>
          <p className="muted">
            Honesty doctrine: this ranks <em>worth a look today</em>, not “buy”. Macro is Razor&rsquo;s
            documented conviction shown with its freshness — not measured alpha. No backtest blesses
            these weights.
          </p>
        </footer>
      </main>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   FRESHNESS BAR
   ───────────────────────────────────────────────────────────────────────── */
function Freshness({
  loading,
  unavailable,
  board,
  source,
  stale,
}: {
  loading: boolean;
  unavailable: BoardUnavailable | null;
  board: Board | null;
  source: BoardSource;
  stale: boolean;
}) {
  if (loading) return <div className="fresh skel">syncing…</div>;
  if (unavailable || !board)
    return (
      <div className="fresh bad">
        <span className="fresh-dot bad" /> data unavailable
      </div>
    );
  return (
    <div className={`fresh ${stale ? "warn" : "ok"}`}>
      <span className={`fresh-dot ${stale ? "warn" : "ok"}`} />
      <span>
        {board.scored}/{board.universe_size} scored
      </span>
      <span className="fresh-sep">·</span>
      <span>{stale ? "⚠ stale · " : ""}generated {fmtRel(board.generated_at)}</span>
      <span className="fresh-sep">·</span>
      <span className="fresh-src">{source === "file" ? "static snapshot" : source}</span>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   NAME CARD — the ranked-board row, with the visible score breakdown
   ───────────────────────────────────────────────────────────────────────── */
function NameCard({
  r,
  open,
  onToggle,
}: {
  r: BoardRow;
  open: boolean;
  onToggle: () => void;
}) {
  const blocks = [
    { ...BLOCK.macro, v: r.blocks.macro },
    { ...BLOCK.fund, v: r.blocks.fundamental ?? null },
    { ...BLOCK.tech, v: r.blocks.technical },
    { ...BLOCK.cat, v: r.blocks.catalyst },
  ];
  const gate = r.blocks.survival_gate;
  const gateTone = gate >= 0.8 ? "green" : gate >= 0.55 ? "amber" : "red";
  const themeMeta = r.theme ? THEME_BY_ID[r.theme] : undefined;
  const accent = r.theme ? themeAccent(r.theme) : "#6b7280";

  return (
    <li className={`card ${open ? "open" : ""} ${r.justBecameAttractive ? "hot" : ""}`}>
      <button className="card-head" onClick={onToggle} aria-expanded={open}>
        <div className="rank-badge" style={{ borderColor: accent }}>
          {r.rank}
        </div>

        <div className="ident">
          <div className="ident-top">
            <span className="tick">{r.ticker}</span>
            {r.justBecameAttractive && <span className="hot-chip">⚡ JUST ATTRACTIVE</span>}
            {(r.fundamental_flag ?? r.detail?.f?.flag) && (
              <span
                className="ff-chip"
                title={(r.fundamental_flag ?? r.detail?.f?.flag) as string}
              >
                ⚠ foreign filer
              </span>
            )}
          </div>
          <div className="ident-sub">
            {r.themed ? (
              <>
                <span className="theme-tag" style={{ color: accent }}>
                  {r.theme}
                </span>
                <span className="theme-name">{r.theme_name}</span>
              </>
            ) : (
              <span className="untheme">un-themed · macro prior 0.40</span>
            )}
          </div>
        </div>

        <div className="composite">
          <div className="comp-num">{r.score.toFixed(1)}</div>
          <div className="comp-label">SCORE</div>
        </div>
      </button>

      {/* ── Layer-2 (Kairos) strip — analyst rank BESIDE mechanical + delta.
          No black box: the formula's order, the analyst's order, and the gap,
          per name, with the one-line WHY + conviction. ─────────────────────── */}
      {r.kairosRank != null && (
        <div className="kairos">
          <div className="k-ranks">
            <span className="k-rank" title="Kairos (analyst) rank">
              <span className="k-rank-tag">KAIROS</span>#{r.kairosRank}
            </span>
            <span className="k-vs">vs</span>
            <span className="k-mech" title="Mechanical (formula) rank">
              <span className="k-mech-tag">FORMULA</span>#{r.mechanicalRank}
            </span>
            {r.kairosDelta != null && r.kairosDelta !== 0 && (
              <span
                className={`k-delta ${r.kairosDelta > 0 ? "up" : "down"}`}
                title="Places Kairos moved this name vs the formula"
              >
                {r.kairosDelta > 0 ? "▲" : "▼"}
                {Math.abs(r.kairosDelta)}
              </span>
            )}
            {r.kairosConviction && (
              <span className={`k-conv ${convClass(r.kairosConviction)}`}>
                {r.kairosConviction}
              </span>
            )}
            {r.kairosProbTier && <span className="k-prob">{r.kairosProbTier}</span>}
          </div>
          {r.kairosWhy && <div className="k-why">“{r.kairosWhy}”</div>}
          {r.kairosCorrelation && (
            <div className="k-corr">⛓ {r.kairosCorrelation}</div>
          )}
          {/* ENTRY-TRIGGER GATE chip (commission 44 A) + "wait for the entry" tag */}
          {r.detail?.e?.entry_state && (
            <div className="entry-state-row">
              <span
                className={`entry-chip es-${r.detail.e.entry_state.toLowerCase()}`}
                title={`entry_trigger ${r.detail.e.entry_trigger ?? "—"} (engine-side gate)`}
              >
                ENTRY {r.detail.e.entry_state}
              </span>
              {r.detail.e.entry_state === "FAIL" && (r.rank ?? 99) <= 5 && (
                <span className="entry-wait">⚠ wait for the entry</span>
              )}
            </div>
          )}
          {/* ENTRY/EXIT LEVELS panel (commission 44 B) — top-N only, zones not calls */}
          {r.levels && (
            <div className="levels">
              <div className="levels-hdr">
                ENTRY / EXIT · suggested zones — not precise calls ·{" "}
                <span className={`lv-conv lv-${r.levels.levels_conviction.toLowerCase()}`}>
                  {r.levels.levels_conviction}
                </span>
              </div>
              <div className="levels-grid">
                <span className="lv-k">now</span>
                <span className="lv-v">${r.levels.current_price.toFixed(2)}</span>
                <span className="lv-k">entry</span>
                <span className="lv-v">
                  ${r.levels.entry_zone[0].toFixed(2)}–{r.levels.entry_zone[1].toFixed(2)}
                </span>
                <span className="lv-k">target</span>
                <span className="lv-v">${r.levels.target.toFixed(2)}</span>
                <span className="lv-k">stop</span>
                <span className="lv-v">${r.levels.stop.toFixed(2)}</span>
                <span className="lv-k">R:R</span>
                <span className="lv-v">
                  {r.levels.rr != null ? `${r.levels.rr.toFixed(1)} : 1` : "—"}
                </span>
              </div>
              {r.levels.flags.length > 0 && (
                <div className="lv-flags">
                  {r.levels.flags.map((f, i) => (
                    <span key={i} className="lv-flag">
                      ⚠ {f}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* visible composite breakdown — stacked weighted bar */}
      <div className="breakdown" onClick={onToggle}>
        <div className="stack">
          {blocks.map((b) => {
            const contrib = (b.v ?? 0.4) * b.w; // weighted contribution to the additive sum
            return (
              <div
                key={b.key}
                className={`seg ${b.v === null ? "seg-na" : ""}`}
                style={{
                  flexGrow: contrib,
                  background: b.v === null ? undefined : b.c,
                }}
                title={`${b.label}: ${b.v === null ? "n/a" : b.v.toFixed(2)} × ${b.w}`}
              />
            );
          })}
          <div className="gate-cap" data-tone={gateTone} title={`Survival gate ×${gate.toFixed(2)}`}>
            ×{gate.toFixed(2)}
          </div>
        </div>
        <div className="micro">
          {blocks.map((b) => (
            <span className="micro-item" key={b.key}>
              <span className="micro-dot" style={{ background: b.c }} />
              {b.v === null ? "n/a" : b.v.toFixed(2)}
            </span>
          ))}
          <span className={`micro-gate ${gateTone}`}>gate {gate.toFixed(2)}</span>
          <span className="micro-expand">{open ? "Hide detail ▲" : "Tap for 14 signals ▼"}</span>
        </div>
      </div>

      {open && <CardDetail r={r} themeMeta={themeMeta} accent={accent} blocks={blocks} />}
    </li>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   CARD DETAIL — 14-signal breakdown + theme thesis
   ───────────────────────────────────────────────────────────────────────── */
function CardDetail({
  r,
  themeMeta,
  accent,
  blocks,
}: {
  r: BoardRow;
  themeMeta: ThemeMeta | undefined;
  accent: string;
  blocks: { key: string; label: string; c: string; w: number; v: number | null }[];
}) {
  const d = r.detail;
  // foreign/IFRS filers carry a flag (no us-gaap XBRL → fundamental can't be computed,
  // 0.50 neutral prior used). Surface it so the prior is honest, not silent.
  const fflag = r.fundamental_flag ?? d?.f?.flag ?? null;
  return (
    <div className="detail">
      {fflag && (
        <div className="ff-flag" role="note">
          ⚠ {fflag}. Fundamental uses a 0.50 neutral prior.
        </div>
      )}
      {/* the four block contributions as labelled bars */}
      <div className="block-bars">
        {blocks.map((b) => (
          <div className="bb-row" key={b.key}>
            <span className="bb-label">{b.label}</span>
            <div className="bb-track">
              <div
                className="bb-fill"
                style={{
                  width: `${(b.v ?? 0.4) * 100}%`,
                  background: b.v === null ? "repeating-linear-gradient(45deg,#3a3f50,#3a3f50 4px,#2a2e3c 4px,#2a2e3c 8px)" : b.c,
                }}
              />
            </div>
            <span className="bb-val">{b.v === null ? "n/a" : b.v.toFixed(2)}</span>
            <span className="bb-w">×{b.w}</span>
          </div>
        ))}
      </div>

      {d && (
        <div className="sig-grid">
          <SigGroup
            title="Fundamental"
            tone={BLOCK.fund.c}
            note={
              fflag
                ? "foreign/IFRS — 0.50 prior"
                : d.f.pre_revenue
                  ? "pre-revenue proxy branch"
                  : `rev TTM $${fmtBig(d.f.rev_ttm)}`
            }
            sigs={
              fflag
                ? [["F · status", "unavailable"], ["F · prior", "0.50"]]
                : d.f.pre_revenue
                  ? [["F · proxy", fmt2(d.f.proxy)]]
                  : [
                      ["F1 rev-accel", fmt2(d.f.components.F1)],
                      ["F2 GM-trend", fmt2(d.f.components.F2)],
                      ["F3 R&D-int.", fmt2(d.f.components.F3)],
                      ["F4 rel-strength", fmt2(d.f.components.F4)],
                    ]
            }
          />
          <SigGroup
            title="Technical timing"
            tone={BLOCK.tech.c}
            note="when it got cheap"
            sigs={[
              ["C1 drawdown", pct(d.t.drawdown)],
              ["C2 RVOL", `${d.t.rvol.toFixed(2)}×`],
              ["C3 gap", pct(d.t.gap)],
              ["C4 RSI(14)", d.t.rsi.toFixed(1)],
            ]}
          />
          <SigGroup
            title="Catalyst"
            tone={BLOCK.cat.c}
            note="reason to re-rate"
            sigs={[
              ["K2 insider buys 90d", String(d.c.form4_buys_90d)],
              ["K3 8-K filings 30d", String(d.c.filings8k_30d)],
              ["WL trigger", d.c.wl_trigger ? "LIVE" : "—"],
            ]}
          />
          {d.e && (
            <SigGroup
              title="Entry timing"
              tone="#f9a8d4"
              note="deployable now? (Kairos inputs)"
              sigs={[
                [
                  "E1 retrace off low",
                  d.e.retrace_off_low == null
                    ? "—"
                    : `${(d.e.retrace_off_low * 100).toFixed(0)}% spent`,
                ],
                [
                  "E2 catalyst",
                  d.e.catalyst_days == null
                    ? "none"
                    : `${d.e.catalyst_days}d${d.e.catalyst_est ? " est." : ""} (${d.e.catalyst_bucket ?? "—"})`,
                ],
                ["E3 dislocation", d.e.dislocation_state],
              ]}
            />
          )}
          <SigGroup
            title="Survival gate"
            tone="#9ca3af"
            note="anti-bagholder multiplier"
            sigs={[
              ["Q1 runway (q)", d.q.runway_q == null ? "—" : `${d.q.runway_q.toFixed(0)}q`],
              ["Q2 dilution", d.q.dilution == null ? "—" : d.q.dilution.toFixed(2)],
              ["gate", r.blocks.survival_gate == null ? "—" : `×${r.blocks.survival_gate.toFixed(2)}`],
            ]}
          />
        </div>
      )}

      {themeMeta && (
        <div className="thesis" style={{ borderColor: hexA(accent, 0.35) }}>
          <div className="thesis-head">
            <span className="thesis-tag" style={{ background: hexA(accent, 0.16), color: accent }}>
              {themeMeta.id}
            </span>
            <span className="thesis-title">{themeMeta.name}</span>
            <span className="thesis-macro" style={{ color: accent }}>
              macro {themeMeta.macro}/100
            </span>
          </div>
          <MacroDims t={themeMeta} accent={accent} />
          <div className="thesis-freshness">
            reviewed {revAge(themeMeta.last_reviewed)}
          </div>
        </div>
      )}
    </div>
  );
}

function SigGroup({
  title,
  tone,
  note,
  sigs,
}: {
  title: string;
  tone: string;
  note: string;
  sigs: [string, string][];
}) {
  return (
    <div className="sig-group">
      <div className="sg-head">
        <span className="sg-dot" style={{ background: tone }} />
        {title}
        <span className="sg-note">{note}</span>
      </div>
      <div className="sg-rows">
        {sigs.map(([k, v]) => (
          <div className="sg-row" key={k}>
            <span className="sg-k">{k}</span>
            <span className="sg-v">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   THEME GRID — the 6 themes with their macro thesis
   ───────────────────────────────────────────────────────────────────────── */
function ThemeGrid({ active, onPick }: { active: string; onPick: (id: string) => void }) {
  return (
    <div className="theme-grid">
      {THEMES.map((t) => {
        const accent = themeAccent(t.id);
        const stale = (daysSince(t.last_reviewed) ?? 0) > STALE_AFTER_DAYS;
        const on = active === t.id;
        return (
          <button
            key={t.id}
            className={`theme-card ${on ? "on" : ""}`}
            style={{ borderColor: on ? accent : undefined }}
            onClick={() => onPick(on ? "" : t.id)}
          >
            <div className="tc-head">
              <span className="tc-id" style={{ background: hexA(accent, 0.16), color: accent }}>
                {t.id}
              </span>
              <span className="tc-name">{t.name}</span>
            </div>
            <div className="tc-macro">
              <span className="tc-macro-num" style={{ color: accent }}>
                {t.macro}
              </span>
              <span className="tc-macro-den">/100</span>
              {stale && <span className="tc-stale">⚠ stale</span>}
            </div>
            <MacroDims t={t} accent={accent} />
            <div className="tc-rev">reviewed {revAge(t.last_reviewed)}</div>
          </button>
        );
      })}
    </div>
  );
}

function MacroDims({ t, accent }: { t: ThemeMeta; accent: string }) {
  const dims = [
    { k: "structural" as const, label: "Structural tailwind", v: t.structural },
    { k: "supply" as const, label: "Supply shock", v: t.supply },
    { k: "winner" as const, label: "Winner-take-most", v: t.winner },
    { k: "policy" as const, label: "Policy / capital", v: t.policy },
  ];
  return (
    <div className="dims">
      {dims.map((d) => (
        <div className="dim" key={d.k}>
          <div className="dim-top">
            <span className="dim-label">{d.label}</span>
            <span className="dim-val">{d.v}/25</span>
          </div>
          <div className="dim-track">
            <div
              className="dim-fill"
              style={{ width: `${(d.v / 25) * 100}%`, background: accent }}
            />
          </div>
          <div className="dim-why">{t.rationale[d.k]}</div>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   HOW IT'S RANKED — plain explainer
   ───────────────────────────────────────────────────────────────────────── */
function HowRanked() {
  return (
    <div className="how">
      <p>
        Every name gets a <strong>composite 0–100 score</strong>. It&rsquo;s built from four
        independent reads on asymmetry, each weighted, then <strong>multiplied down by a survival
        gate</strong> so a fragile balance sheet can&rsquo;t bagload the top:
      </p>
      <div className="how-formula">
        <code>
          score = 100 × <span style={{ color: "#9ca3af" }}>survival</span> × ( 0.30·
          <span style={{ color: BLOCK.macro.c }}>macro</span> + 0.25·
          <span style={{ color: BLOCK.fund.c }}>fundamental</span> + 0.25·
          <span style={{ color: BLOCK.tech.c }}>technical</span> + 0.20·
          <span style={{ color: BLOCK.cat.c }}>catalyst</span> )
        </code>
      </div>
      <ul className="how-list">
        <li>
          <b style={{ color: BLOCK.macro.c }}>Macro (30%)</b> — the theme&rsquo;s structural “oxygen”:
          is demand inflecting, is supply shocked, does it concentrate into a few winners, is policy
          money flowing. Razor&rsquo;s maintained conviction — the only judgement input, shown with
          its freshness.
        </li>
        <li>
          <b style={{ color: BLOCK.fund.c }}>Fundamental (25%)</b> — is <em>this</em> name winning the
          consolidation: revenue-growth acceleration, gross-margin trend, R&amp;D intensity, relative
          strength. EDGAR-sourced; pre-revenue names use a cash-runway proxy.
        </li>
        <li>
          <b style={{ color: BLOCK.tech.c }}>Technical timing (25%)</b> — <em>when</em> it got cheap:
          drawdown, relative volume, gap, RSI. The trigger, not the thesis.
        </li>
        <li>
          <b style={{ color: BLOCK.cat.c }}>Catalyst (20%)</b> — a reason to re-rate: insider buying,
          filing cadence, a live watchlist trigger.
        </li>
        <li>
          <b>Survival gate (×0.3–1.0)</b> — cash runway &amp; dilution. Multiplies the whole score
          <em> down</em> — the anti-bagholder rule.
        </li>
      </ul>
      <p className="how-foot">
        <b>⚡ Just became attractive</b> fires when a name that&rsquo;s already top-quartile on
        macro+fundamental <em>also</em> printed a fresh technical dislocation — something we believe
        in structurally just got cheap.
      </p>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   AMBIENT BACKGROUND
   ───────────────────────────────────────────────────────────────────────── */
function Aurora() {
  return (
    <div className="aurora" aria-hidden>
      <span className="blob b1" />
      <span className="blob b2" />
      <span className="blob b3" />
    </div>
  );
}

/* ── helpers ──────────────────────────────────────────────────────────────── */
const THEME_ACCENTS: Record<string, string> = {
  T1: "#34d399", // nuclear — green energy
  T2: "#fb923c", // rare-earth — ore orange
  T3: "#38bdf8", // optics — photon blue
  T4: "#a3a3a3", // defence — steel
  T5: "#a78bfa", // space — violet
  T6: "#f472b6", // power-semis — magenta
};
function themeAccent(id: string): string {
  return THEME_ACCENTS[id] ?? "#6b7280";
}
function hexA(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const n = parseInt(h, 16);
  const r = (n >> 16) & 255,
    g = (n >> 8) & 255,
    b = n & 255;
  return `rgba(${r},${g},${b},${a})`;
}
function fmt2(v: number | null | undefined): string {
  return v === null || v === undefined ? "n/a" : v.toFixed(2);
}
function convClass(conv: string): string {
  const c = conv.toUpperCase();
  if (c.startsWith("HIGH")) return "high";
  if (c.startsWith("MODERATE-HIGH")) return "modhigh";
  if (c.startsWith("MODERATE")) return "mod";
  if (c.startsWith("LOW-MODERATE")) return "lowmod";
  return "low";
}
function pct(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}
function fmtBig(v: number | null): string {
  if (v === null) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
  return v.toFixed(0);
}
function revAge(iso: string): string {
  const d = daysSince(iso);
  if (d === null) return iso;
  if (d <= 0) return "today";
  if (d === 1) return "1 day ago";
  return `${d} days ago`;
}
function fmtRel(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// keep convictionScore import referenced (used by board decoration; re-exported here for
// potential future client-side re-sort). Silences unused-import in strict builds.
void convictionScore;
