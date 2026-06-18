# themes.md — Theme macro-asymmetry register (Razor-maintained)

> A1 is the ONLY judgement signal in the board. Each theme carries a 0–100 macro
> score = sum of four 0–25 dimensions (Structural tailwind / Supply-shock / Winner-take-most /
> Policy). Reviewed every 2 weeks + event-driven. If `last_reviewed` is older than the
> cadence window the board flags the theme **STALE** (amber). Append-only history at bottom.
>
> Screen-tranche (un-themed) names get a neutral macro prior of 0.40 (40/100), flagged un-themed.
>
> The machine-readable copy is `themes.json` (same numbers). Edit BOTH together.
> Dimension rationales are one line each, sourced — no naked numbers.

| id | theme | macro | structural | supply | winner | policy | last_reviewed | seed_tickers |
|----|-------|-------|-----------|--------|--------|--------|---------------|--------------|
| T1 | Nuclear & AI-power | 82 | 24 | 20 | 20 | 18 | 2026-06-17 | LEU, OKLO, SMR, NNE, CCJ |
| T2 | Critical minerals / rare-earth | 78 | 18 | 24 | 18 | 18 | 2026-06-17 | MP, USAR |
| T3 | AI optics / interconnect (CPO) | 74 | 22 | 16 | 20 | 16 | 2026-06-17 | AAOI, COHR, LITE, ALAB, POET |
| T4 | Defence & autonomous systems | 70 | 18 | 14 | 16 | 22 | 2026-06-17 | AVAV, KTOS, ONDS |
| T5 | Space economy | 68 | 22 | 12 | 20 | 14 | 2026-06-17 | RKLB, ASTS, LUNR, RDW |
| T6 | Power-semis / electrification | 64 | 20 | 14 | 14 | 16 | 2026-06-17 | NVTS, WOLF, ON, MPWR |

## Dimension rationales (sourced)

### T1 Nuclear & AI-power — 82
- Structural 24: datacentre electricity demand is a step-change not a cycle (hyperscaler PPAs, WATCHLIST nuclear thesis).
- Supply 20: HALEU enrichment supply is chokepoint-constrained; Russia ban forces domestic build.
- Winner 20: LEU near-monopoly on Western HALEU; OKLO/SMR concentrate the SMR buildout.
- Policy 18: DOE awards, ADVANCE Act, reactor-deployment mandates flowing capital in.

### T2 Critical minerals / rare-earth — 78
- Structural 18: EV + defence + magnet demand inflecting on electrification.
- Supply 24: China export crackdown = engineered scarcity (the textbook supply-shock).
- Winner 18: MP is the only scaled Western mine-to-magnet; USAR early.
- Policy 18: DoD price-floor offtake, IRA, allied-sourcing mandates.

### T3 AI optics / interconnect (CPO) — 74
- Structural 22: AI cluster scale-out forces optical interconnect bandwidth step-change.
- Supply 16: laser/photonics capacity tight but not chokepoint-grade.
- Winner 20: CPO transition concentrates into a few photonics names (COHR, LITE, ALAB).
- Policy 16: CHIPS-adjacent, but less direct mandate than nuclear/RE.

### T4 Defence & autonomous systems — 70
- Structural 18: drone/autonomy doctrine shift post-Ukraine is durable.
- Supply 14: less of a supply chokehold story.
- Winner 16: fragmented field; AVAV/KTOS lead but not winner-take-most.
- Policy 22: NATO 2–3% GDP mandate; replenishment budgets — strongest policy tailwind.

### T5 Space economy — 68
- Structural 22: launch-cost collapse + LEO constellation demand inflecting.
- Supply 12: SpaceX "sucks the oxygen" — squeezes margin for the rest (LOW supply-asymmetry FOR the squeezed names).
- Winner 20: direct-to-cell (ASTS) and small-launch (RKLB) carve defensible niches.
- Policy 14: NASA/Space Force awards, but SpaceX dominance caps the policy upside for others.

### T6 Power-semis / electrification — 64
- Structural 20: SiC/GaN adoption in EV + datacentre power inflecting.
- Supply 14: SiC substrate capacity tight (WOLF's own pain) but not engineered scarcity.
- Winner 14: crowded — ON/MPWR/Infineon incumbents vs WOLF/NVTS challengers.
- Policy 16: CHIPS + EV credits, moderate.

## Append-only history
- 2026-06-17 — initial scores set by Razor for the 6 LOCKED themes (brief `04-RAZOR-BRIEF-v2.md`). Seeds = brief tickers. No prior state.
