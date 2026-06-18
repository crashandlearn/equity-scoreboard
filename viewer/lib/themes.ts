import type { ThemeMeta } from "./types";

// Public-safe mirror of data/themes.json + the one-line sourced dimension
// rationales from data/themes.md. This is Razor's documented macro conviction —
// a JUDGEMENT layer, surfaced transparently with its freshness. No market data,
// no secrets: it is the "oxygen factor" thesis text the board is honest about.
//
// Keep in sync with ../../data/themes.json and ../../data/themes.md when scores
// or rationales change.

export const NEUTRAL_MACRO_PRIOR = 40;
export const STALE_AFTER_DAYS = 14;

export const THEMES: ThemeMeta[] = [
  {
    id: "T1",
    name: "Nuclear & AI-power",
    short: "Nuclear",
    macro: 82,
    structural: 24,
    supply: 20,
    winner: 20,
    policy: 18,
    last_reviewed: "2026-06-17",
    seed_tickers: ["LEU", "OKLO", "SMR", "NNE", "CCJ"],
    rationale: {
      structural:
        "Datacentre electricity demand is a step-change not a cycle (hyperscaler PPAs, WATCHLIST nuclear thesis).",
      supply: "HALEU enrichment supply is chokepoint-constrained; Russia ban forces domestic build.",
      winner: "LEU near-monopoly on Western HALEU; OKLO/SMR concentrate the SMR buildout.",
      policy: "DOE awards, ADVANCE Act, reactor-deployment mandates flowing capital in.",
    },
  },
  {
    id: "T2",
    name: "Critical minerals / rare-earth",
    short: "Rare-earth",
    macro: 78,
    structural: 18,
    supply: 24,
    winner: 18,
    policy: 18,
    last_reviewed: "2026-06-17",
    seed_tickers: ["MP", "USAR"],
    rationale: {
      structural: "EV + defence + magnet demand inflecting on electrification.",
      supply: "China export crackdown = engineered scarcity (the textbook supply-shock).",
      winner: "MP is the only scaled Western mine-to-magnet; USAR early.",
      policy: "DoD price-floor offtake, IRA, allied-sourcing mandates.",
    },
  },
  {
    id: "T3",
    name: "AI optics / interconnect (CPO)",
    short: "Optics",
    macro: 74,
    structural: 22,
    supply: 16,
    winner: 20,
    policy: 16,
    last_reviewed: "2026-06-17",
    seed_tickers: ["AAOI", "COHR", "LITE", "ALAB", "POET"],
    rationale: {
      structural: "AI cluster scale-out forces optical interconnect bandwidth step-change.",
      supply: "Laser/photonics capacity tight but not chokepoint-grade.",
      winner: "CPO transition concentrates into a few photonics names (COHR, LITE, ALAB).",
      policy: "CHIPS-adjacent, but less direct mandate than nuclear/RE.",
    },
  },
  {
    id: "T4",
    name: "Defence & autonomous systems",
    short: "Defence",
    macro: 70,
    structural: 18,
    supply: 14,
    winner: 16,
    policy: 22,
    last_reviewed: "2026-06-17",
    seed_tickers: ["AVAV", "KTOS", "ONDS"],
    rationale: {
      structural: "Drone/autonomy doctrine shift post-Ukraine is durable.",
      supply: "Less of a supply chokehold story.",
      winner: "Fragmented field; AVAV/KTOS lead but not winner-take-most.",
      policy: "NATO 2–3% GDP mandate; replenishment budgets — strongest policy tailwind.",
    },
  },
  {
    id: "T5",
    name: "Space economy",
    short: "Space",
    macro: 68,
    structural: 22,
    supply: 12,
    winner: 20,
    policy: 14,
    last_reviewed: "2026-06-17",
    seed_tickers: ["RKLB", "ASTS", "LUNR", "RDW"],
    rationale: {
      structural: "Launch-cost collapse + LEO constellation demand inflecting.",
      supply: "SpaceX “sucks the oxygen” — squeezes margin for the rest (low supply-asymmetry for the squeezed names).",
      winner: "Direct-to-cell (ASTS) and small-launch (RKLB) carve defensible niches.",
      policy: "NASA/Space Force awards, but SpaceX dominance caps the policy upside for others.",
    },
  },
  {
    id: "T6",
    name: "Power-semis / electrification",
    short: "Power-semis",
    macro: 64,
    structural: 20,
    supply: 14,
    winner: 14,
    policy: 16,
    last_reviewed: "2026-06-17",
    seed_tickers: ["NVTS", "WOLF", "ON", "MPWR"],
    rationale: {
      structural: "SiC/GaN adoption in EV + datacentre power inflecting.",
      supply: "SiC substrate capacity tight (WOLF's own pain) but not engineered scarcity.",
      winner: "Crowded — ON/MPWR/Infineon incumbents vs WOLF/NVTS challengers.",
      policy: "CHIPS + EV credits, moderate.",
    },
  },
];

export const THEME_BY_ID: Record<string, ThemeMeta> = Object.fromEntries(
  THEMES.map((t) => [t.id, t])
);

// Days since an ISO date (for the macro-freshness "reviewed Nd ago" cell).
export function daysSince(iso: string): number | null {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86_400_000);
}
