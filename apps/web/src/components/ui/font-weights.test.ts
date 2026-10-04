// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * NFR-A11Y-001, docs/17 §3.1: PP Mori ships weights 200/400/600 only. `font-medium` (500)
 * renders as 400, so text meant to be emphasised looks regular; `font-bold` (700) and
 * `font-extrabold` (800) render as a synthetic or snapped 600. Emphasis is `font-semibold`;
 * everything else uses the default weight. Every source file is checked: there is no
 * allowlist, and none may be added.
 */

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../..");
const self = fileURLToPath(import.meta.url);

const BANNED = /(?<![\w-])font-(medium|bold|extrabold)(?![\w-])/g;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

function withoutComments(text: string): string {
  // Keep the newlines so the reported line numbers stay right.
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ""))
    .replace(/^\s*\/\/.*$/gm, "");
}

describe("font weights (docs/17 §3.1)", () => {
  it("no source file uses font-medium, font-bold or font-extrabold", () => {
    const sources = files(src).filter((file) => /\.(tsx?|css)$/.test(file) && file !== self);
    expect(sources.length).toBeGreaterThan(100);
    // The swept feature folders are scanned too (a guard against a narrowed walk).
    for (const folder of ["imports", "documents", "sheets", "marketing"]) {
      expect(
        sources.some((file) =>
          relative(src, file).split("\\").join("/").startsWith(`features/${folder}/`),
        ),
      ).toBe(true);
    }
    const offenders: string[] = [];
    for (const file of sources) {
      const path = relative(src, file).split("\\").join("/");
      const text = withoutComments(readFileSync(file, "utf8"));
      for (const match of text.matchAll(BANNED)) {
        const line = text.slice(0, match.index).split("\n").length;
        offenders.push(`${path}:${line} ${match[0]}`);
      }
    }
    expect(offenders, "use font-semibold for emphasis, or the default weight").toEqual([]);
  });
});
