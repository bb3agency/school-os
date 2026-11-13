// @vitest-environment node
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import en from "../../messages/en.json";
import { filesUnder, keysFrom, keysIn, srcPath, uncovered } from "@/test/namespace-scan";
import { CLIENT_NAMESPACES, pickMessages, type ClientScope } from "./client-messages";
import { SCHOOL_AREAS, type SchoolArea } from "./school-areas";

/**
 * WP-04 (audit 2026-11-08, NFR-PERF; PERF-013..015): the browser gets only the message paths
 * of the area it is in. Each area's list must hold every path that code reachable from its
 * layouts and pages asks for (scanned statically; app/english-only.test.tsx checks the same at
 * render time).
 */

const L = "app/[locale]";
const SCHOOL = `${L}/(school)`;
const MARKETING = ["about", "features", "pricing", "security", "welcome"];
const AREAS = Object.keys(SCHOOL_AREAS) as SchoolArea[];

const ENTRIES = {
  root: () => [
    srcPath(L, "layout.tsx"),
    srcPath(L, "not-found.tsx"),
    ...["[...rest]", "choose-school", "no-access", "signed-out", "step-up-complete"].flatMap((p) =>
      filesUnder(`${L}/${p}`),
    ),
  ],
  // The school layout alone: the shell, banners and footer around every school page.
  schoolBase: () => [srcPath(SCHOOL, "layout.tsx")],
  platform: () => filesUnder(`${L}/platform`),
  marketing: () => MARKETING.flatMap((p) => filesUnder(`${L}/${p}`)),
  dev: () => filesUnder(`${L}/dev`),
  ...Object.fromEntries(
    AREAS.map((area) => [
      area,
      () => (area === "home" ? [srcPath(SCHOOL, "page.tsx")] : filesUnder(`${SCHOOL}/${area}`)),
    ]),
  ),
} as Record<ClientScope, () => string[]>;

const size = (value: unknown) => JSON.stringify(value).length;
const WHOLE = size(en);
const share = (scope: ClientScope) => size(pickMessages(en, CLIENT_NAMESPACES[scope])) / WHOLE;

/**
 * Budgets per school area, as a share of the whole en catalogue (measured 2026-11-13 at
 * 1cd65ade plus this split; PERF-015). Each is about 0.03 above its measurement so a feature
 * can grow without a test change, and a large jump (a whole namespace pulled into an area)
 * fails and shows up in review. Raise one only with a note in the PR.
 */
const AREA_BUDGET: Record<SchoolArea, number> = {
  home: 0.16, // 0.129
  apaar: 0.16, // 0.128
  ask: 0.19, // 0.158
  attendance: 0.15, // 0.116
  audit: 0.09, // 0.055
  "break-glass": 0.1, // 0.064
  brief: 0.06, // 0.023
  certificates: 0.15, // 0.110
  "change-requests": 0.13, // 0.092
  circulars: 0.17, // 0.131
  completeness: 0.08, // 0.050
  compliance: 0.13, // 0.095
  consents: 0.17, // 0.135
  documents: 0.18, // 0.141
  exports: 0.19, // 0.151
  fees: 0.32, // 0.288
  findings: 0.16, // 0.127
  flags: 0.12, // 0.083
  "follow-up-counts": 0.06, // 0.021
  imports: 0.18, // 0.141
  marks: 0.12, // 0.088
  messages: 0.17, // 0.137
  notices: 0.19, // 0.157
  notifications: 0.05, // 0.020
  privacy: 0.17, // 0.136
  "register-photos": 0.15, // 0.114
  registers: 0.09, // 0.055
  "settings/billing": 0.09, // 0.052
  "settings/consent-purposes": 0.11, // 0.072
  "settings/data-export": 0.11, // 0.072
  "settings/disclosure": 0.14, // 0.103
  "settings/fees": 0.17, // 0.136
  "settings/payments": 0.11, // 0.077
  "settings/privacy": 0.13, // 0.096
  "settings/retention": 0.11, // 0.077
  "settings/school": 0.1, // 0.067
  "settings/structure": 0.12, // 0.090
  "settings/tally": 0.2, // 0.169
  "settings/upi": 0.17, // 0.136
  "settings/users": 0.11, // 0.078
  "settings/whatsapp": 0.14, // 0.108
  students: 0.31, // 0.270
  support: 0.1, // 0.062
  tasks: 0.12, // 0.081
  "year-end": 0.11, // 0.080
};

describe("message paths (PERF-013)", () => {
  const catalogue = { a: { nav: { x: "X" }, list: { y: "Y" }, z: "Z" }, b: { c: "C" } };

  it("picks namespaces and subtrees at their own path, without changing the source", () => {
    expect(pickMessages(catalogue, ["a.nav", "b", "a.missing", "nope"])).toEqual({
      a: { nav: { x: "X" } },
      b: { c: "C" },
    });
    // A subtree under a namespace that is sent whole adds nothing.
    expect(pickMessages(catalogue, ["a.nav", "a"])).toEqual({ a: catalogue.a });
    expect(catalogue.a).toEqual({ nav: { x: "X" }, list: { y: "Y" }, z: "Z" });
  });

  it("scans translation calls, namespace props and full keys", () => {
    const keys = (code: string) => [...keysIn(code, catalogue)].sort();
    expect(keys('useTranslations("a.nav"); t("x")')).toEqual(["a.nav"]);
    expect(keys("useTranslations(`a.${part}`)")).toEqual(["a"]);
    expect(keys('<ApiErrorAlert namespace="b" />')).toEqual(["b"]);
    expect(keys('t("a.list.y"); t(`a.nav.${k}`); t("a.list." + k)')).toEqual([
      "a.list",
      "a.list.y",
      "a.nav",
    ]);
    // An unknown key under a known subtree keeps the subtree; a permission name is no key.
    expect(keys('t("a.nav.typo"); require("a.read")')).toEqual(["a.nav"]);
    expect(uncovered(["a.nav.x", "a.list", "b"], ["a.nav", "b"])).toEqual(["a.list"]);
  });
});

describe("client message namespaces per area (WP-04)", () => {
  for (const scope of Object.keys(ENTRIES) as ClientScope[]) {
    it(`${scope}: every message path its components use is sent`, () => {
      const used = keysFrom(ENTRIES[scope](), en);
      const declared: readonly string[] = CLIENT_NAMESPACES[scope];
      expect(uncovered(used, declared)).toEqual([]);
      // Every listed path exists in the catalogue.
      expect(declared.filter((name) => Object.keys(pickMessages(en, [name])).length === 0)).toEqual(
        [],
      );
    });
  }

  it("every school folder is an area whose layout sends that area's messages", () => {
    const root = srcPath(SCHOOL);
    const folders = fs
      .readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .flatMap((entry) =>
        // The settings pages are one area per subfolder (settings itself has no page).
        entry.name === "settings"
          ? fs.readdirSync(path.join(root, "settings")).map((sub) => `settings/${sub}`)
          : [entry.name],
      );
    expect(folders.sort()).toEqual(AREAS.filter((area) => area !== "home").sort());
    expect(fs.existsSync(path.join(root, "settings", "page.tsx"))).toBe(false);
    for (const area of folders) {
      const layout = fs.readFileSync(path.join(root, area, "layout.tsx"), "utf8");
      expect(layout, area).toContain(`<ClientMessages scope="${area}">`);
    }
    expect(fs.readFileSync(path.join(root, "layout.tsx"), "utf8")).toContain(
      '<ClientMessages scope="schoolBase">',
    );
    expect(fs.readFileSync(path.join(root, "page.tsx"), "utf8")).toContain(
      '<ClientMessages scope="home">',
    );
  });

  it("the root and the marketing pages do not carry the consoles' texts", () => {
    for (const big of ["platform", "fees", "school", "marketing", "messaging", "ask", "public"]) {
      expect(CLIENT_NAMESPACES.root).not.toContain(big);
    }
    // "ui": the pricing comparison table names its scroll region (WA-03).
    expect(CLIENT_NAMESPACES.marketing).toEqual(["common", "marketing", "ui"]);
    expect(CLIENT_NAMESPACES.platform).not.toContain("fees");
    // The shell needs the menu labels of the areas, never their screens' texts.
    for (const big of ["fees", "messaging", "ask", "privacy", "school", "students", "errors"]) {
      expect(CLIENT_NAMESPACES.schoolBase).not.toContain(big);
    }
    // The parent page texts are rendered on the server only, never sent.
    for (const [scope, list] of Object.entries(CLIENT_NAMESPACES)) {
      const names: readonly string[] = list;
      expect(names.some((name) => name === "public" || name.startsWith("public."))).toBe(false);
      expect(names.some((name) => name === "platform" || name.startsWith("platform."))).toBe(
        scope === "platform",
      );
      if (scope !== "dev") expect(names).not.toContain("devUi");
    }
  });

  it("is much smaller than the whole catalogue", () => {
    expect(share("root")).toBeLessThan(0.1);
    expect(share("marketing")).toBeLessThan(0.1);
    expect(share("platform")).toBeLessThan(0.35);
    // PERF-013: the school shell's messages (0.073 measured 2026-11-13; the whole school list
    // was 0.85 before the split, D86).
    expect(share("schoolBase")).toBeLessThan(0.1);
  });

  it.each(AREAS)("school area %s stays within its budget (PERF-015)", (area) => {
    expect(share(area)).toBeLessThan(AREA_BUDGET[area]);
  });

  it("a school page sends less than the old school-wide list (PERF-014)", () => {
    // What one school page carries: the shell's list plus its area's list (0.361 at most,
    // fees, measured 2026-11-13).
    for (const area of AREAS) {
      expect(share("schoolBase") + share(area), area).toBeLessThan(0.42);
    }
  });
});
