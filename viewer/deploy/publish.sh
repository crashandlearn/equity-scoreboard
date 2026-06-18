#!/usr/bin/env bash
# publish.sh — ONE-COMMAND public deploy to GitHub Pages (crashandlearn account).
#
# Publishes the built static `out/` to the gh-pages branch of a PUBLIC repo using
# `gh`. Razor gates the deploy — do not run this until cleared.
#
#   Usage:  REPO=crashandlearn/equity-scoreboard ./deploy/publish.sh
#
# WALL: this targets the crashandlearn (public) account, NOT rzrtrdr/razor (the
# private Pit). The published artefact is signals + a composite score only — no
# secrets, no account data, no order rail. `gh auth status` must show the
# crashandlearn account active before running.
set -euo pipefail

REPO="${REPO:-crashandlearn/equity-scoreboard}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/out"

[[ -d "$OUT" ]] || { echo "✗ no out/ — run deploy/refresh-board.sh first"; exit 1; }

# Jekyll would eat the _next/ dir; .nojekyll disables it.
touch "$OUT/.nojekyll"

echo "→ publishing $OUT to $REPO (gh-pages)…"
cd "$OUT"
git init -q
git checkout -q -B gh-pages
git add -A
git commit -q -m "publish equity-opportunity scoreboard $(date -u +%Y-%m-%dT%H:%MZ)"
git push -f "https://github.com/$REPO.git" gh-pages

echo "✓ pushed. In the repo settings enable Pages → branch: gh-pages, /(root)."
echo "  Public URL: https://${REPO%%/*}.github.io/${REPO##*/}/"
