import type { NextConfig } from "next";

// Equity Opportunity Scoreboard viewer — standalone, isolated, READ-ONLY.
// No money-path import, no IBKR, no broker, no order rail. It renders the static
// board.json the engine emits. Visibility only — nothing here can place an order.
//
// Static export: `next build` emits a fully static site to ./out, deployable to
// GitHub Pages. The board is static (HTML/CSS/JS + a board.json), so there is no
// server, no secret, no API surface in the public artefact. The page fetches
// board.json client-side at runtime.
//
// BASE_PATH lets it live at a repo subpath on Pages
// (e.g. https://crashandlearn.github.io/equity-scoreboard).

const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig: NextConfig = {
  output: "export",
  reactStrictMode: true,
  basePath: basePath || undefined,
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
