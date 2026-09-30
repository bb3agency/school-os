/**
 * Noto Sans Telugu, self-hosted (no font CDN), loaded only while Telugu is switched on
 * (ADR-0036). With it off, no Telugu `@font-face`, preload or font-family stack reaches the
 * browser: globals.css has none, and the locale layout links this stylesheet only when
 * `teluguEnabled()`. The route handler under /fonts/telugu serves the stylesheet and the font
 * files (the Telugu subset of @fontsource/noto-sans-telugu) and answers 404 while it is off.
 */

export const TELUGU_FONT_BASE = "/fonts/telugu";
export const TELUGU_STYLESHEET = "noto-sans-telugu.css";
export const TELUGU_STYLESHEET_HREF = `${TELUGU_FONT_BASE}/${TELUGU_STYLESHEET}`;

const WEIGHTS = [400, 600, 700] as const;

/** The font files served, Telugu script only (Latin text uses Inter). */
export const TELUGU_FONT_FILES: readonly string[] = WEIGHTS.map(
  (weight) => `noto-sans-telugu-telugu-${weight}-normal.woff2`,
);

// The same unicode-range as @fontsource/noto-sans-telugu's Telugu subset.
const UNICODE_RANGE = "U+0951-0952,U+0964-0965,U+0C00-0C7F,U+1CDA,U+1CF2,U+200C-200D,U+25CC";

/**
 * The stylesheet: the font faces, and the sans and mono stacks with Noto Sans Telugu as the
 * fallback for Telugu glyphs (the stacks in globals.css, plus Telugu). Unlayered, so it wins
 * over the Tailwind theme layer whatever the order the two stylesheets load in.
 */
export function teluguStylesheet(): string {
  const faces = WEIGHTS.map(
    (weight) => `@font-face {
  font-family: "Noto Sans Telugu";
  font-style: normal;
  font-display: swap;
  font-weight: ${weight};
  src: url(${TELUGU_FONT_BASE}/noto-sans-telugu-telugu-${weight}-normal.woff2) format("woff2");
  unicode-range: ${UNICODE_RANGE};
}`,
  );
  return `/* Telugu (ADR-0036: served only while SOS_TELUGU_ENABLED is on). */
${faces.join("\n")}
:root {
  --font-sans: "Inter Variable", "Noto Sans Telugu", system-ui, "Segoe UI", Arial, sans-serif;
  --font-mono:
    "JetBrains Mono", ui-monospace, Consolas, "Liberation Mono", "Noto Sans Telugu", monospace;
}
`;
}
