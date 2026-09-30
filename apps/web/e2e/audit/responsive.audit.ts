/**
 * Responsive layout sweep (NFR-A11Y-001, NFR-I18N-001): every screen of
 * e2e/support/responsive.ts at many viewports and both languages, with a JSON report and
 * screenshots in AUDIT_OUT. It measures the same things as e2e/responsive.spec.ts (which
 * gates `make e2e` at 1366×768 and 375×812) and fails when a REQUIRED viewport has a
 * problem; the other viewports are reported only. Run it against a running app with the
 * stand-in IdP and API (see audit.config.ts).
 *
 * Knobs: AUDIT_VP ("375x812,1366x768"), AUDIT_LOCALES ("en", or "en,te" when the run sets
 * SOS_TELUGU_ENABLED=true: ADR-0036), AUDIT_ONLY (regex on the
 * URL), AUDIT_SHOTS (widths to screenshot, "" for none), AUDIT_OUT (default audit-out).
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { installFixtures } from "../support/layout-fixtures";
import {
  REQUIRED_VIEWPORTS,
  SCREEN_GROUPS,
  measure,
  problems,
  settle,
  signInAs,
  type LayoutReport,
} from "../support/responsive";

const OUT = process.env.AUDIT_OUT ?? "audit-out";
const SHOTS = new Set(
  (process.env.AUDIT_SHOTS ?? "375,1366").split(",").filter(Boolean).map(Number),
);
const ONLY = process.env.AUDIT_ONLY ? new RegExp(process.env.AUDIT_ONLY) : null;
const LOCALES = (
  process.env.AUDIT_LOCALES ?? (process.env.SOS_TELUGU_ENABLED === "true" ? "en,te" : "en")
).split(",");
const VIEWPORTS = (
  process.env.AUDIT_VP ??
  "360x740,375x812,390x844,414x896,768x1024,1024x768,1280x800,1366x768,1440x900,1920x1080"
)
  .split(",")
  .map((size) => size.split("x").map(Number) as [number, number]);
const required = (w: number, h: number) =>
  REQUIRED_VIEWPORTS.some(([rw, rh]) => rw === w && rh === h);

for (const [groupName, group] of Object.entries(SCREEN_GROUPS)) {
  test(`audit ${groupName}`, async ({ page }) => {
    test.setTimeout(90 * 60_000);
    mkdirSync(join(OUT, "shots"), { recursive: true });
    if (group.subject) {
      await installFixtures(page);
      await page.setViewportSize({ width: 1366, height: 768 });
      await signInAs(page, group.signInPath, group.subject);
    }
    const results: Record<string, LayoutReport> = {};
    const failures: string[] = [];
    for (const locale of LOCALES) {
      for (const path of group.pages) {
        const url = `/${locale}${path}`;
        if (ONLY && !ONLY.test(url)) continue;
        for (const [w, h] of VIEWPORTS) {
          // A fresh load at each size: some layout is chosen when the page starts.
          await page.setViewportSize({ width: w, height: h });
          await page.goto(url);
          await settle(page);
          const report = await measure(page);
          results[`${url} @${w}x${h}`] = report;
          if (required(w, h)) {
            for (const problem of problems(report)) failures.push(`${url} @${w}x${h}: ${problem}`);
          }
          if (SHOTS.has(w)) {
            const name = `${url.replace(/[/?=&]/g, "_")}_${w}.png`;
            await page.screenshot({ path: join(OUT, "shots", name), fullPage: true });
          }
        }
      }
    }
    writeFileSync(join(OUT, `${groupName}.json`), JSON.stringify(results, null, 1));
    expect(failures).toEqual([]);
  });
}
