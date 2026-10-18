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

/**
 * OKLCH → sRGB hex (CSS Color 4: OKLab → linear sRGB, gamma-encoded, clamped to the sRGB
 * gamut, which is what an sRGB office screen shows). The palette is written in oklch()
 * (docs/17 §3); the contrast maths below works on sRGB.
 */
function oklchToHex(lightness: number, chroma: number, hue: number): string {
  const h = (hue * Math.PI) / 180;
  const a = chroma * Math.cos(h);
  const b = chroma * Math.sin(h);
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const linear = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return `#${linear
    .map((value) => {
      const clamped = Math.min(1, Math.max(0, value));
      const encoded = clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055;
      return Math.round(encoded * 255)
        .toString(16)
        .padStart(2, "0");
    })
    .join("")}`;
}

const tokens = new Map<string, string>();
for (const match of theme.matchAll(
  /--color-([a-z0-9-]+):\s*(?:(#[0-9a-f]{6})\b|oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\))/gi,
)) {
  tokens.set(
    match[1] as string,
    match[2]
      ? match[2].toLowerCase()
      : oklchToHex(Number(match[3]), Number(match[4]), Number(match[5])),
  );
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
  // Status pills: solid tints with strong text (docs/17 §4, no gradients)
  ["info-ink", "info-soft", 4.5, "In progress pill"],
  ["violet-ink", "violet-soft", 4.5, "Review pill"],
  ["teal-ink", "teal-soft", 4.5, "Done pill"],
  ["positive-ink", "positive-soft", 4.5, "Positive pill"],
  ["danger", "danger-soft", 4.5, "Negative pill"],
  ["primary", "info-soft", 4.5, "Date pill"],
  ["ink-muted", "surface-muted", 4.5, "Tag pill"],
  ["ink-muted", "surface", 4.5, "Sample pill"],
  // Neutral canvas behind dense record screens (docs/17 §3.2)
  ["ink", "canvas-neutral", 4.5, "text on the neutral canvas"],
  ["ink-muted", "canvas-neutral", 4.5, "secondary text on the neutral canvas"],
  ["ink-subtle", "canvas-neutral", 4.5, "notes on the neutral canvas"],
  ["primary", "canvas-neutral", 4.5, "links and breadcrumbs on the neutral canvas"],
  ["focus", "canvas-neutral", 3, "focus ring on the neutral canvas"],
  // Platform chrome
  ["platform-ink", "platform", 4.5, "platform sidebar and top bar text"],
  ["platform-ink", "platform-hover", 4.5, "platform sidebar hover and active row"],
  ["platform-muted", "platform", 4.5, "platform sidebar items, headings, role line"],
  ["platform-accent", "platform", 3, "platform focus ring and active marker"],
  ["platform-accent-ink", "platform-accent", 4.5, "platform badge"],
  ["platform", "platform-soft", 4.5, "active platform nav item"],
  // The one sidebar (docs/17 §5.2)
  ["primary", "primary-soft", 3, "sidebar active bar on the active row"],
  ["ink-subtle", "surface-muted", 4.5, "'Current school' label"],
  ["primary", "surface-muted", 4.5, "'Switch school' link on the school block"],
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
  // Quiet ledger (docs/17 v1.0): the one accent, the flat canvas and the fixed status colours
  ["ink", "canvas", 4.5, "text on the flat canvas"],
  ["ink-muted", "canvas", 4.5, "secondary text on the flat canvas"],
  ["ink-subtle", "canvas", 4.5, "notes and group labels on the canvas"],
  ["ink-subtle", "surface-sunken", 4.5, "notes on the sunken track"],
  ["accent", "surface", 4.5, "links, breadcrumb links on white"],
  ["accent", "canvas", 4.5, "links and the top bar breadcrumb on the canvas"],
  ["accent", "accent-soft", 4.5, "current nav item, selected row text"],
  ["accent-strong", "accent-soft", 4.5, "selected chip text"],
  ["on-accent", "accent", 4.5, "selection count badge, accent fill"],
  ["on-accent", "accent-strong", 4.5, "accent fill hover"],
  ["accent", "accent-soft", 3, "current nav item marker on its tint"],
  ["focus", "canvas", 3, "focus ring on the flat canvas"],
  ["focus", "surface-muted", 3, "focus ring on table headers and filled inputs"],
  ["success-ink", "success-soft", 4.5, "success pill and toast"],
  ["warning-ink", "warning-soft", 4.5, "warning pill and toast"],
  ["danger", "danger-soft", 4.5, "danger pill, error summary"],
  ["info-ink", "info-soft", 4.5, "info pill and toast"],
  ["ink", "surface-hover", 4.5, "row text on hover"],
  ["ink-muted", "surface-hover", 4.5, "secondary row text on hover"],
  ["border-control", "canvas", 3, "checkbox boundary on the canvas"],
  ["platform-muted", "platform-hover", 4.5, "operator sidebar muted text on hover"],
  ["platform-ink", "platform", 4.5, "operator top bar breadcrumb"],
  ["ink", "accent-soft", 4.5, "current sidebar item, palette option text"],
  ["ink-muted", "accent-soft", 4.5, "filter chip label"],
  ["accent-strong", "accent-soft", 4.5, "active palette option, filter chip value"],
  ["warning-ink", "surface", 4.5, "'Changes not saved' on the save bar"],
  ["ink-subtle", "surface-muted", 4.5, "palette group headings, footer hints"],
  ["ink-muted", "surface-sunken", 4.5, "sidebar item on hover"],
  ["platform-accent", "platform-hover", 3, "operator focus ring on the active row"],
];

describe("design tokens meet WCAG 2.2 AA (NFR-A11Y-001)", () => {
  it("parses the palette from globals.css", () => {
    expect(tokens.size).toBeGreaterThan(40);
    // Quiet ledger: warm near-black ink, ink-blue accent, flat warm canvas.
    expect(hex("ink")).toBe("#1c1a17");
    expect(hex("accent")).toBe("#2f62ac");
    expect(hex("canvas")).toBe("#fbfaf9");
  });

  it("the canvas is flat: the old gradient stops equal the canvas", () => {
    for (const name of ["canvas-from", "canvas-via", "canvas-to", "canvas-neutral"]) {
      expect(hex(name), name).toBe(hex("canvas"));
    }
    expect(css).not.toMatch(/body\s*\{[^}]*linear-gradient/);
  });

  it("there is one accent: primary, brand, focus and info point at it", () => {
    for (const name of ["primary", "brand", "focus", "chart-1", "info-soft"]) {
      expect(hex(name), name).toBe(name === "info-soft" ? hex("accent-soft") : hex("accent"));
    }
  });

  it("converts oklch like the browser (known sRGB values)", () => {
    expect(oklchToHex(1, 0, 0)).toBe("#ffffff");
    expect(oklchToHex(0, 0, 0)).toBe("#000000");
    expect(oklchToHex(0.627955, 0.257683, 29.234)).toBe("#ff0000");
  });

  it.each(CONTRAST_PAIRS)("%s on %s ≥ %s:1 (%s)", (foreground, background, minimum) => {
    expect(contrast(hex(foreground), hex(background))).toBeGreaterThanOrEqual(minimum);
  });

  it("the ratio computation matches known values", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
  });
});

/** `--radius-<name>` values in px from a block of CSS (rem × 16). */
function radii(block: string): Map<string, number> {
  const found = new Map<string, number>();
  for (const match of block.matchAll(/--radius-([a-z0-9]+):\s*([\d.]+)(rem|px)\b/g)) {
    const value = Number(match[2]) * (match[3] === "rem" ? 16 : 1);
    found.set(match[1] as string, value);
  }
  return found;
}

describe("radius tokens: calm app corners, larger marketing corners (docs/17 §3)", () => {
  const app = radii(theme);
  const marketingCss = readFileSync(
    resolve(dirname(fileURLToPath(import.meta.url)), "../../features/marketing/marketing.css"),
    "utf8",
  );
  const mkStart = marketingCss.indexOf(".mk {");
  const marketing = radii(marketingCss.slice(mkStart, marketingCss.indexOf("}", mkStart)));

  it("cards, dialogs and panels in the app are 10-14px (rounded-xl, rounded-2xl)", () => {
    for (const name of ["lg", "xl", "2xl"]) {
      expect(app.get(name), name).toBeGreaterThanOrEqual(10);
      expect(app.get(name), name).toBeLessThanOrEqual(14);
    }
  });

  it("inputs and buttons (rounded-md) are scaled to match: smaller than the cards", () => {
    expect(app.get("md")).toBeGreaterThanOrEqual(6);
    expect(app.get("md")).toBeLessThan(app.get("xl") ?? 0);
    expect(app.get("sm")).toBeLessThanOrEqual(app.get("md") ?? 0);
  });

  it("the marketing pages (.mk) keep their larger radii", () => {
    expect(marketing.get("md")).toBe(10);
    expect(marketing.get("lg")).toBe(14);
    expect(marketing.get("xl")).toBe(20);
    expect(marketing.get("2xl")).toBe(24);
    // rounded-3xl (closing bands) exists only because the theme defines it.
    expect(app.has("3xl")).toBe(true);
    expect(marketing.get("3xl")).toBe(32);
  });
});
