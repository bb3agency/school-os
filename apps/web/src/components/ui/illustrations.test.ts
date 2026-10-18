import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ILLUSTRATIONS } from "./Illustration";

/**
 * Lint for the illustration set (docs/17 §5.6; skills-review.md §5 "SVG file contract",
 * ported from the MengTo lint rules instead of running their scripts): static, CSP-safe,
 * self-contained, small, and drawn only from the illustration palette.
 */
const DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../../public/illustrations");
const files = readdirSync(DIR).filter((file) => file.endsWith(".svg"));

/** Line ink, paper, wood, steel, the ledger green and amber accents, soft tints, 4 skin tones. */
const PALETTE = new Set([
  "#2b2a26",
  "#fbfaf5",
  "#ffffff",
  "#ecdcc0",
  "#d9c29b",
  "#e6e6e0",
  "#cfcfc6",
  "#0e6549",
  "#d9f0e3",
  "#e0a43c",
  "#f7e5bb",
  "#f2d4cc",
  "#e3ecef",
  "#a29e93",
  "#5d5a52",
  "#f3cdb0",
  "#e2a57c",
  "#b47852",
  "#7a4b33",
]);

describe("illustrations (public/illustrations/*.svg)", () => {
  it("every file is registered in ILLUSTRATIONS and every entry has a file", () => {
    expect(files.map((file) => file.replace(/\.svg$/, "")).sort()).toEqual(
      Object.keys(ILLUSTRATIONS).sort(),
    );
    expect(files.length).toBeGreaterThanOrEqual(4);
    expect(files.length).toBeLessThanOrEqual(6);
  });

  for (const file of files) {
    const svg = readFileSync(join(DIR, file), "utf8");

    it(`${file}: at most 30 KB`, () => {
      expect(statSync(join(DIR, file)).size).toBeLessThanOrEqual(30 * 1024);
    });

    it(`${file}: no script, styles, classes, handlers or external references (CSP)`, () => {
      expect(svg).not.toMatch(/<script/i);
      expect(svg).not.toMatch(/<style/i);
      expect(svg).not.toMatch(/\sstyle=/i);
      expect(svg).not.toMatch(/\sclass=/i);
      expect(svg).not.toMatch(/\son[a-z]+=/i);
      expect(svg).not.toMatch(/<(image|foreignObject|use|a)\b/i);
      expect(svg).not.toMatch(/(xlink:)?href=/i);
      expect(svg).not.toMatch(/url\(/i);
      expect(svg).not.toMatch(/https?:\/\/(?!www\.w3\.org\/2000\/svg")/i);
    });

    it(`${file}: one 480×360 drawing with a title, presentation attributes only`, () => {
      expect(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 360"')).toBe(
        true,
      );
      expect(svg.match(/<svg\b/g)).toHaveLength(1);
      expect(svg).toMatch(/<title id="[a-z-]+-title">[^<]{10,}<\/title>/);
      // No gradients, filters, masks or opacity: line art that prints and stays light.
      expect(svg).not.toMatch(/<(linearGradient|radialGradient|filter|mask|pattern)\b/i);
      expect(svg).not.toMatch(/opacity=/i);
    });

    it(`${file}: colours come from the illustration palette`, () => {
      const colours = [...svg.matchAll(/(?:fill|stroke)="(#[0-9a-f]{6})"/gi)].map((m) =>
        m[1]!.toLowerCase(),
      );
      expect(colours.length).toBeGreaterThan(0);
      expect(colours.filter((colour) => !PALETTE.has(colour))).toEqual([]);
    });
  }
});
