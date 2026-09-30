import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * NFR-A11Y-001 (WCAG 2.2 AA): every documented foreground/background token pair is
 * re-computed from the hex values in globals.css, so a palette change that breaks
 * contrast fails here. The same table is in docs/17-ui-design-system.md.
 */
const css = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../../app/globals.css"),
  "utf8",
);

// Only the @theme block defines tokens (scoped overrides such as .platform-chrome come later).
const theme = css.slice(css.indexOf("@theme {"), css.indexOf("@layer base"));
const tokens = new Map<string, string>();
for (const match of theme.matchAll(/--color-([a-z0-9-]+):\s*(#[0-9a-f]{6})\b/gi)) {
  tokens.set(match[1] as string, (match[2] as string).toLowerCase());
}

function hex(name: string): string {
  const value = tokens.get(name);
  if (!value) throw new Error(`Unknown colour token --color-${name}`);
  return value;
}

function luminance(color: string): number {
  const channels = [1, 3, 5].map((index) => parseInt(color.slice(index, index + 2), 16) / 255);
  const [r, g, b] = channels.map((value) =>
    value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  ) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

/** [foreground, background, minimum ratio, where it is used]. */
const CONTRAST_PAIRS: ReadonlyArray<readonly [string, string, number, string]> = [
  // Text on cards and the canvas
  ["ink", "surface", 4.5, "body text on cards"],
  ["ink", "canvas-from", 4.5, "text on the canvas"],
  ["ink-muted", "surface", 4.5, "secondary text on cards"],
  ["ink-muted", "canvas-from", 4.5, "secondary text on the canvas"],
  ["ink-muted", "canvas-to", 4.5, "secondary text on the canvas"],
  ["ink-muted", "surface-muted", 4.5, "table header, filled input"],
  ["ink-muted", "surface-sunken", 4.5, "unselected segment"],
  ["ink-subtle", "surface", 4.5, "comparison line on KPI cards"],
  ["ink-subtle", "canvas-from", 4.5, "notes on the canvas"],
  ["ink-subtle", "canvas-to", 4.5, "notes on the canvas"],
  ["ink-subtle", "surface-muted", 4.5, "placeholder in filled inputs"],
  ["ink", "surface-muted", 4.5, "typed text in filled inputs"],
  // Links and brand
  ["primary", "surface", 4.5, "links on cards"],
  ["primary", "canvas-from", 4.5, "links on the canvas"],
  ["primary", "primary-soft", 4.5, "active nav item"],
  ["primary", "info-soft", 4.5, "date chip"],
  ["on-primary", "primary", 4.5, "legacy brand fill"],
  ["white", "brand", 4.5, "brand button"],
  ["white", "brand-strong", 4.5, "brand button hover"],
  ["on-action", "action", 4.5, "primary (near-black) button, delta pill"],
  ["on-action", "action-hover", 4.5, "primary button hover"],
  // Status
  ["white", "danger", 4.5, "danger button"],
  ["danger", "danger-soft", 4.5, "danger alert, negative chip"],
  ["danger", "surface", 4.5, "error text"],
  ["warning-ink", "warning-soft", 4.5, "warning alert"],
  ["success-ink", "success-soft", 4.5, "success alert"],
  ["positive-ink", "positive-soft", 4.5, "positive chip"],
  ["info-ink", "info-soft", 4.5, "info alert"],
  ["violet-ink", "violet-soft", 4.5, "violet chip"],
  ["teal-ink", "teal-soft", 4.5, "teal chip"],
  // Gradient pills: white text on the lighter stop is the worst case
  ["white", "pill-blue-from", 4.5, "In progress pill (dark stop)"],
  ["white", "pill-blue-to", 4.5, "In progress pill (light stop)"],
  ["white", "pill-violet-from", 4.5, "Review pill (dark stop)"],
  ["white", "pill-violet-to", 4.5, "Review pill (light stop)"],
  ["white", "pill-teal-from", 4.5, "Done pill (dark stop)"],
  ["white", "pill-teal-to", 4.5, "Done pill (light stop)"],
  ["white", "ai-from", 4.5, "AI panel text (dark stop)"],
  ["white", "ai-to", 4.5, "AI panel text (light stop)"],
  // Platform chrome
  ["platform-ink", "platform", 4.5, "platform rail text"],
  ["platform-ink", "platform-hover", 4.5, "platform rail hover"],
  ["platform-muted", "platform", 4.5, "platform rail secondary text"],
  ["platform-accent", "platform", 3, "platform focus ring and active marker"],
  ["platform-accent-ink", "platform-accent", 4.5, "platform badge"],
  ["platform", "platform-soft", 4.5, "active platform nav item"],
  // The one sidebar (docs/17 §5.2)
  ["ink-muted", "surface", 4.5, "sidebar item text and icon"],
  ["ink", "surface-muted", 4.5, "sidebar item hover"],
  ["primary", "primary-soft", 3, "sidebar active bar on the active row"],
  ["ink-subtle", "surface", 4.5, "sidebar section heading, role line"],
  ["ink-subtle", "surface-muted", 4.5, "'Current school' label"],
  ["primary", "surface-muted", 4.5, "'Switch school' link on the school block"],
  ["platform-muted", "platform", 4.5, "platform sidebar item text, headings"],
  ["platform-ink", "platform-hover", 4.5, "platform sidebar active and hover row"],
  ["platform-accent", "platform-hover", 3, "platform sidebar active bar"],
  ["on-action", "action", 4.5, "compact sidebar label (tooltip)"],
  // UI boundaries and graphics (WCAG 1.4.11: 3:1)
  ["border-control", "surface", 3, "input and switch boundary on cards"],
  ["border-control", "surface-muted", 3, "input boundary against its fill"],
  ["border-strong", "surface", 3, "legacy control boundary"],
  ["focus", "surface", 3, "focus ring on cards"],
  ["focus", "canvas-from", 3, "focus ring on the canvas"],
  ["success", "surface", 3, "switch on, timeline check"],
  ["chart-1", "surface", 3, "chart series 1"],
  ["chart-2", "surface", 3, "chart series 2 (amber)"],
  ["chart-3", "surface", 3, "chart series 3 (teal)"],
  ["chart-4", "surface", 3, "chart series 4 (violet)"],
];

describe("design tokens meet WCAG 2.2 AA (NFR-A11Y-001)", () => {
  it("parses the palette from globals.css", () => {
    expect(tokens.size).toBeGreaterThan(40);
    expect(hex("ink")).toBe("#111827");
  });

  it.each(CONTRAST_PAIRS)("%s on %s ≥ %s:1 (%s)", (foreground, background, minimum) => {
    expect(contrast(hex(foreground), hex(background))).toBeGreaterThanOrEqual(minimum);
  });

  it("the ratio computation matches known values", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
  });
});
