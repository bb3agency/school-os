import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";
import en from "../../messages/en.json";
import te from "../../messages/te.json";
import { flattenMessages, icuSignature } from "@/test/icu";

const catalogs = { en: flattenMessages(en), te: flattenMessages(te) } as const;
const TELUGU = /[ఀ-౿]/;

describe("NFR-I18N-001 message catalogues", () => {
  it("en and te have exactly the same keys", () => {
    const enKeys = [...catalogs.en.keys()].sort();
    const teKeys = [...catalogs.te.keys()].sort();
    const missingInTe = enKeys.filter((key) => !catalogs.te.has(key));
    const extraInTe = teKeys.filter((key) => !catalogs.en.has(key));
    expect({ missingInTe, extraInTe }).toEqual({ missingInTe: [], extraInTe: [] });
  });

  it("every message is a non-empty string", () => {
    for (const [locale, catalog] of Object.entries(catalogs)) {
      for (const [key, value] of catalog) {
        expect(value.trim(), `${locale}:${key}`).not.toBe("");
      }
    }
  });

  it("ICU arguments, types and plural/select branches match between en and te", () => {
    const mismatches: string[] = [];
    for (const [key, enMessage] of catalogs.en) {
      const teMessage = catalogs.te.get(key);
      if (teMessage === undefined) continue;
      const a = icuSignature(enMessage);
      const b = icuSignature(teMessage);
      if (JSON.stringify(a) !== JSON.stringify(b)) {
        mismatches.push(`${key}: en ${JSON.stringify(a)} ≠ te ${JSON.stringify(b)}`);
      }
    }
    expect(mismatches).toEqual([]);
  });

  it("every message formats without ICU errors in both locales", () => {
    for (const locale of ["en", "te"] as const) {
      const messages = locale === "en" ? en : te;
      const errors: string[] = [];
      const t = createTranslator({
        locale,
        messages,
        timeZone: "Asia/Kolkata",
        onError: (error) => errors.push(error.message),
      }) as unknown as (key: string, values?: Record<string, string | number>) => string;
      for (const [key, message] of catalogs[locale]) {
        const values: Record<string, string | number> = {};
        for (const arg of icuSignature(message).args) {
          const [name, type] = arg.split(":") as [string, string];
          values[name] =
            type === "plural" || type === "number" || type === "selectordinal" ? 2 : "x";
        }
        const output = t(key, values);
        expect(output, `${locale}:${key}`).not.toContain("{");
      }
      expect(errors, locale).toEqual([]);
    }
  });

  it("the Telugu catalogue is actually translated", () => {
    const values = [...catalogs.te.values()];
    const translated = values.filter((value) => TELUGU.test(value)).length;
    // Brand names, codes and examples (SchoolOS, GSTIN, {percent}%) may stay in Latin script.
    expect(translated / values.length).toBeGreaterThan(0.9);
  });

  it("uses sentence case in English headings (no Title Case Words)", () => {
    const titleCase = /^(?:[A-Z][a-z]+\s){2,}[A-Z][a-z]+$/;
    const offenders = [...catalogs.en.entries()].filter(([, value]) => titleCase.test(value));
    expect(offenders).toEqual([]);
  });
});

describe("icuSignature", () => {
  it("collects nested plural arguments and selectors", () => {
    expect(icuSignature("{count, plural, one {# field of {total}} other {# fields}}")).toEqual({
      args: ["count:plural", "total:string"],
      selectors: ["count:one", "count:other"],
    });
  });

  it("ignores apostrophes in normal text and rejects unbalanced braces", () => {
    expect(icuSignature("The school's {page}").args).toEqual(["page:string"]);
    expect(() => icuSignature("Broken {page")).toThrow();
  });
});
