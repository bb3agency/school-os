import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Direction B "Register paper" contrast (WCAG 2.2 AA; docs/17 §5.6, §7): the `.mk` token
 * overrides in marketing.css and the hex palette of the parent and verification pages
 * (app/(public)/public.css) are re-computed here, so a token edit cannot slip below AA.
 */
const here = dirname(fileURLToPath(import.meta.url));
const marketingCss = readFileSync(resolve(here, "marketing.css"), "utf8");
const publicCss = readFileSync(resolve(here, "../../app/(public)/public.css"), "utf8");

function oklchToRgb(l: number, c: number, h: number): [number, number, number] {
  const a = c * Math.cos((h * Math.PI) / 180);
  const b = c * Math.sin((h * Math.PI) / 180);
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const linear = [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ].map((v) => Math.min(1, Math.max(0, v)));
  return linear as [number, number, number];
}

function hexToLinear(hex: string): [number, number, number] {
  const value = hex.replace("#", "");
  return [0, 2, 4].map((i) => {
    const v = parseInt(value.slice(i, i + 2), 16) / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
}

const luminance = ([r, g, b]: [number, number, number]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
function ratio(a: [number, number, number], b: [number, number, number]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** The custom properties declared in the first `.mk {` block. */
function mkTokens(): Map<string, [number, number, number]> {
  const start = marketingCss.indexOf(".mk {");
  const block = marketingCss.slice(start, marketingCss.indexOf("\n}", start));
  const tokens = new Map<string, [number, number, number]>();
  for (const m of block.matchAll(/--([\w-]+):\s*oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)/g)) {
    tokens.set(m[1]!, oklchToRgb(Number(m[2]), Number(m[3]), Number(m[4])));
  }
  return tokens;
}

function publicTokens(): Map<string, [number, number, number]> {
  const tokens = new Map<string, [number, number, number]>();
  for (const m of publicCss.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)) {
    tokens.set(m[1]!, hexToLinear(m[2]!));
  }
  return tokens;
}

const WHITE: [number, number, number] = [1, 1, 1];

describe("Register paper contrast (marketing .mk tokens)", () => {
  const t = mkTokens();
  const pairs: Array<[string, string, number]> = [
    ["color-ink", "color-canvas", 4.5],
    ["color-ink-muted", "color-canvas", 4.5],
    ["color-ink-subtle", "color-canvas", 4.5],
    ["color-ink-subtle", "color-surface-muted", 4.5],
    ["color-ink-muted", "color-surface", 4.5],
    ["color-primary", "color-canvas", 4.5],
    ["color-primary", "color-surface", 4.5],
    ["color-primary", "color-primary-soft", 4.5],
    ["color-info-ink", "color-info-soft", 4.5],
    ["color-border-control", "color-surface", 3],
    ["color-focus", "color-canvas", 3],
    ["mk-night-muted", "mk-night", 4.5],
  ];
  for (const [fg, bg, min] of pairs) {
    it(`${fg} on ${bg} ≥ ${min}:1`, () => {
      expect(t.get(fg), fg).toBeDefined();
      expect(t.get(bg), bg).toBeDefined();
      expect(ratio(t.get(fg)!, t.get(bg)!)).toBeGreaterThanOrEqual(min);
    });
  }
  it("white on the action button and on the night band ≥ 4.5:1", () => {
    expect(ratio(WHITE, t.get("color-action")!)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(WHITE, t.get("mk-night")!)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("Register paper contrast (parent and verification pages)", () => {
  const t = publicTokens();
  const pairs: Array<[string, string, number]> = [
    ["ink", "paper", 4.5],
    ["muted", "paper", 4.5],
    ["muted", "sheet", 4.5],
    ["ledger", "paper", 4.5],
    ["ledger", "sheet", 4.5],
    ["ledger-strong", "ledger-soft", 4.5],
    ["amber-ink", "amber-soft", 4.5],
    ["red-ink", "red-soft", 4.5],
    ["red-ink", "sheet", 4.5],
    ["control", "sheet", 3],
    ["control", "paper", 3],
  ];
  for (const [fg, bg, min] of pairs) {
    it(`${fg} on ${bg} ≥ ${min}:1`, () => {
      expect(t.get(fg), fg).toBeDefined();
      expect(t.get(bg), bg).toBeDefined();
      expect(ratio(t.get(fg)!, t.get(bg)!)).toBeGreaterThanOrEqual(min);
    });
  }
  it("white on the ledger-green button ≥ 4.5:1", () => {
    expect(ratio(WHITE, t.get("ledger")!)).toBeGreaterThanOrEqual(4.5);
  });
});
