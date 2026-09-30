import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cssEasing,
  DURATION,
  EASE_DRAWER,
  EASE_IN_OUT,
  EASE_OUT,
  prefersReducedMotion,
  seconds,
} from "./motion";

/**
 * Motion tokens (docs/17 §5.5, NFR-A11Y-001): CSS (globals.css) and Motion (lib/motion.ts)
 * use the same curves and durations, UI motion stays under 300ms, and every console motion
 * rule is gated by prefers-reduced-motion.
 */
const css = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../app/globals.css"),
  "utf8",
);

function cssToken(name: string): string {
  const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(css);
  if (!match) throw new Error(`--${name} is not defined in globals.css`);
  return (match[1] as string).trim();
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("motion tokens (docs/17 §5.5)", () => {
  it("CSS curves are the ones Motion uses", () => {
    expect(cssToken("ease-out")).toBe(cssEasing(EASE_OUT));
    expect(cssToken("ease-in-out")).toBe(cssEasing(EASE_IN_OUT));
    expect(cssToken("ease-drawer")).toBe(cssEasing(EASE_DRAWER));
    expect(cssEasing(EASE_OUT)).toBe("cubic-bezier(0.23, 1, 0.32, 1)");
  });

  it("CSS durations are the ones Motion uses, all under 300ms, exits faster than entrances", () => {
    for (const [name, ms] of Object.entries(DURATION)) {
      expect(cssToken(`duration-${name}`)).toBe(`${ms}ms`);
      expect(ms).toBeLessThan(300);
    }
    expect(DURATION.quick).toBeLessThan(DURATION.enter);
    expect(seconds("enter")).toBe(0.2);
  });

  it("the ease-out curve is defined once (no second, drifting copy)", () => {
    expect(css.match(/--ease-out:/g)).toHaveLength(1);
  });

  it("console motion rules sit behind prefers-reduced-motion: no-preference", () => {
    const block = css.slice(css.indexOf("Console motion (docs/17 §5.5)"));
    for (const selector of [
      "dialog.dialog-motion[open] {",
      ".drawer[open] {",
      ".alert-in,",
      ".content-in {",
      ".pressable:active",
    ]) {
      const at = block.indexOf(selector);
      expect(at, selector).toBeGreaterThan(-1);
      const before = block.slice(0, at);
      expect(before.lastIndexOf("prefers-reduced-motion: no-preference"), selector).toBeGreaterThan(
        -1,
      );
    }
    // No `transition: all`, no ease-in on UI, never scale(0).
    expect(css).not.toMatch(/transition:\s*all/);
    expect(css).not.toMatch(/\bease-in\b(?!-out)/);
    expect(css).not.toMatch(/scale\(0\)/);
  });

  it("prefersReducedMotion reads the media query and is false without matchMedia", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(prefersReducedMotion()).toBe(false);
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({ matches: query === "(prefers-reduced-motion: reduce)" })),
    );
    expect(prefersReducedMotion()).toBe(true);
  });
});
