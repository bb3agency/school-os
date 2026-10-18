import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";
import en from "../../messages/parent.en.json";
import te from "../../messages/parent.te.json";
import { flattenMessages, icuSignature } from "@/test/icu";

/**
 * The parent page catalogue (wave R2-3H; FR-PUB-008; ADR-0044): parent.en.json and
 * parent.te.json have the same keys and ICU arguments, and every message formats. The Telugu file
 * is unreviewed until a fluent reviewer signs it off (D23; app/core/parent_texts.yaml
 * `public.pages`).
 */

const catalogs = { en: flattenMessages(en), te: flattenMessages(te) } as const;

describe("parent page catalogue", () => {
  it("en and te have exactly the same keys and ICU arguments", () => {
    expect([...catalogs.te.keys()].sort()).toEqual([...catalogs.en.keys()].sort());
    for (const [key, message] of catalogs.en) {
      expect(icuSignature(catalogs.te.get(key) ?? ""), key).toEqual(icuSignature(message));
    }
  });

  it("every message is non-empty and formats in both languages", () => {
    for (const locale of ["en", "te"] as const) {
      const t = createTranslator({
        locale,
        messages: locale === "en" ? en : te,
        timeZone: "Asia/Kolkata",
      }) as unknown as (key: string, values?: Record<string, string>) => string;
      for (const [key, message] of catalogs[locale]) {
        expect(message.trim(), `${locale}:${key}`).not.toBe("");
        const values = Object.fromEntries(
          icuSignature(message).args.map((a) => [a.split(":")[0]!, "x"]),
        );
        expect(t(key, values), `${locale}:${key}`).not.toContain("{");
      }
    }
  });
});
