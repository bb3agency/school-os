// @vitest-environment node
import { describe, expect, it } from "vitest";
import en from "../../messages/en.json";
import { filesUnder, namespacesFrom, srcPath } from "@/test/namespace-scan";
import { CLIENT_NAMESPACES, pickMessages, type ClientScope } from "./client-messages";

/**
 * WP-04 (audit 2026-11-08, NFR-PERF): the browser gets only the message namespaces of the area
 * it is in. Each area's list must hold every namespace that code reachable from its layouts and
 * pages asks for (scanned statically; app/english-only.test.tsx checks the same at render time).
 */

const TOP = new Set(Object.keys(en));
const L = "app/[locale]";
const MARKETING = ["about", "features", "pricing", "security", "welcome"];

const ENTRIES: Record<ClientScope, () => string[]> = {
  root: () => [
    srcPath(L, "layout.tsx"),
    srcPath(L, "not-found.tsx"),
    ...["[...rest]", "choose-school", "no-access", "signed-out", "step-up-complete"].flatMap((p) =>
      filesUnder(`${L}/${p}`),
    ),
  ],
  school: () => filesUnder(`${L}/(school)`),
  platform: () => filesUnder(`${L}/platform`),
  marketing: () => MARKETING.flatMap((p) => filesUnder(`${L}/${p}`)),
  dev: () => filesUnder(`${L}/dev`),
};

describe("client message namespaces per area (WP-04)", () => {
  for (const scope of Object.keys(ENTRIES) as ClientScope[]) {
    it(`${scope}: every namespace its components use is sent`, () => {
      const used = namespacesFrom(ENTRIES[scope](), TOP);
      const declared = new Set<string>(CLIENT_NAMESPACES[scope]);
      expect([...used].filter((name) => !declared.has(name)).sort()).toEqual([]);
      // Every listed namespace exists in the catalogue.
      expect([...declared].filter((name) => !TOP.has(name))).toEqual([]);
    });
  }

  it("the root and the marketing pages do not carry the consoles' texts", () => {
    for (const big of ["platform", "fees", "school", "marketing", "messaging", "ask", "public"]) {
      expect(CLIENT_NAMESPACES.root).not.toContain(big);
    }
    // "ui": the pricing comparison table names its scroll region (WA-03).
    expect(CLIENT_NAMESPACES.marketing).toEqual(["common", "marketing", "ui"]);
    expect(CLIENT_NAMESPACES.school).not.toContain("platform");
    expect(CLIENT_NAMESPACES.platform).not.toContain("fees");
    // The parent page texts are rendered on the server only, never sent.
    for (const [scope, list] of Object.entries(CLIENT_NAMESPACES)) {
      const names: readonly string[] = list;
      expect(names).not.toContain("public");
      if (scope !== "dev") expect(names).not.toContain("devUi");
    }
  });

  it("is much smaller than the whole catalogue", () => {
    const size = (value: unknown) => JSON.stringify(value).length;
    const whole = size(en);
    expect(size(pickMessages(en, CLIENT_NAMESPACES.root))).toBeLessThan(whole * 0.1);
    expect(size(pickMessages(en, CLIENT_NAMESPACES.marketing))).toBeLessThan(whole * 0.1);
    expect(size(pickMessages(en, CLIENT_NAMESPACES.platform))).toBeLessThan(whole * 0.35);
    // The school console sends every namespace but platform, marketing and public; it grows with
    // each school feature (0.85 until wave F3, D86). Splitting it per area is a follow-up (WP-04).
    expect(size(pickMessages(en, CLIENT_NAMESPACES.school))).toBeLessThan(whole * 0.87);
  });
});
