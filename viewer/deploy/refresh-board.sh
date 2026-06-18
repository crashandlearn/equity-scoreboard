#!/usr/bin/env bash
# refresh-board.sh — drop the engine's latest board.json into the site, in one place.
# The public site is STATIC: it is the built `out/` directory + a board.json. To
# publish a fresh snapshot you only need to (1) re-run the engine, (2) copy the
# board.json in, (3) rebuild + republish. This script does steps 2–3 locally.
#
# VISIBILITY ONLY — board.json carries signals + a composite score, no account
# data, no keys, no order rail. Public-safe by construction.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"          # .../viewer
SRC="${1:-$HERE/../outputs/board.json}"           # engine output (default)

if [[ ! -f "$SRC" ]]; then
  echo "✗ no board.json at $SRC — run the engine first (engine/run_board.py)"
  exit 1
fi

cp "$SRC" "$HERE/public/board.json"
echo "✓ board.json → public/ ($(wc -c < "$HERE/public/board.json") bytes)"

cd "$HERE"
echo "→ building static export…"
npx next build >/dev/null
echo "✓ static site in $HERE/out  (deploy with deploy/publish.sh)"
