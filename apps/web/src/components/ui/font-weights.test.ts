// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * NFR-A11Y-001, docs/17 §3.1: PP Mori ships weights 200/400/600 only. `font-medium` (500)
 * renders as 400, so text meant to be emphasised looks regular; `font-bold` (700) and
 * `font-extrabold` (800) render as a synthetic or snapped 600. Emphasis is `font-semibold`;
 * everything else uses the default weight.
 */

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../..");
const self = fileURLToPath(import.meta.url);

/**
 * Files not yet swept (owned by other work in flight). Fix them and remove them from this
 * list; never add a file outside these feature folders.
 */
const NOT_YET_SWEPT = new Set([
  // features/imports: spreadsheet onboarding
  "features/imports/ImportsScreen.tsx",
  "features/imports/parts.tsx",
  // features/documents
  "features/documents/DocumentsScreen.tsx",
  "features/documents/NewDocumentScreen.tsx",
  "features/documents/parts.tsx",
  // features/marketing: public pages
  "features/marketing/AboutView.tsx",
  "features/marketing/FeaturesView.tsx",
  "features/marketing/PlanCards.tsx",
  "features/marketing/PricingView.tsx",
  "features/marketing/SiteHeader.tsx",
  "features/marketing/mockups.tsx",
  "features/marketing/HomeView.tsx",
  "features/marketing/ui.tsx",
]);

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
  it("the allowlist names only feature folders outside the sweep", () => {
    for (const path of NOT_YET_SWEPT) {
      expect(path).toMatch(/^features\/(imports|documents|sheets|marketing)\//);
    }
  });

  it("no source file uses font-medium, font-bold or font-extrabold", () => {
    const sources = files(src).filter((file) => /\.(tsx?|css)$/.test(file) && file !== self);
    expect(sources.length).toBeGreaterThan(100);
    const offenders: string[] = [];
    for (const file of sources) {
      const path = relative(src, file).split("\\").join("/");
      if (NOT_YET_SWEPT.has(path)) continue;
      const text = withoutComments(readFileSync(file, "utf8"));
      for (const match of text.matchAll(BANNED)) {
        const line = text.slice(0, match.index).split("\n").length;
        offenders.push(`${path}:${line} ${match[0]}`);
      }
    }
    expect(offenders, "use font-semibold for emphasis, or the default weight").toEqual([]);
  });
});
