import Scoreboard from "./Scoreboard";

// Static shell. The board is fetched client-side from the co-located board.json
// (GitHub Pages serves it statically). READ-ONLY — there is no order rail, no
// broker connection, no API surface, no write path anywhere in this artefact.
export default function Page() {
  return <Scoreboard />;
}
