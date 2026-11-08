import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";
import en from "../../messages/en.json";
import te from "../../messages/te.json";

/**
 * WCAG 2.5.3 Label in Name (NFR-A11Y-001; audit WA-01): a button or link whose `aria-label`
 * replaces its visible text must contain that text, so a speech-input user who says what they
 * see ("click Stop link") hits it. axe-core reports `label-content-name-mismatch` only in a real
 * browser (jsdom has no layout, so the rule is "incomplete" there), so the pairs the product
 * renders are pinned here for both catalogues: [aria-label key, params, visible text key].
 */
const PAIRS: readonly [string, Record<string, string>, string][] = [
  [
    "messaging.revokeLink.rowLabel",
    { purpose: "Fee reminder", when: "01/06/2026 10:00" },
    "messaging.revokeLink.open",
  ],
  ["invitations.acceptLabel", { school: "Sample School" }, "invitations.accept"],
  ["invitations.declineLabel", { school: "Sample School" }, "invitations.decline"],
  ["ask.memory.confirmFor", { text: "Exams" }, "ask.memory.confirm"],
  ["ask.memory.dismissFor", { text: "Exams" }, "ask.memory.dismiss"],
  ["imports.library.downloadFor", { name: "Students" }, "imports.library.download"],
  ["platform.invoices.downloadFor", { number: "INV-1" }, "platform.invoices.download"],
  ["tally.ledgers.linkLedger", { ledger: "Ledger A" }, "tally.ledgers.link"],
  ["devSignIn.signInAs", { role: "Clerk", code: "SCH1" }, "devSignIn.signIn"],
];

const normalize = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}]+/gu, " ")
    .trim();

describe("accessible names contain the visible text (WCAG 2.5.3, WA-01)", () => {
  it.each(["en", "te"] as const)("%s", (locale) => {
    const t = createTranslator({ locale, messages: locale === "te" ? te : en });
    const mismatches = PAIRS.flatMap(([labelKey, params, visibleKey]) => {
      // Keys are pinned above; the translator's typed keys do not cover a dynamic list.
      const label = (t as unknown as (key: string, values?: object) => string)(labelKey, params);
      const visible = (t as unknown as (key: string) => string)(visibleKey);
      return normalize(label).includes(normalize(visible))
        ? []
        : [`${labelKey}: "${label}" ∌ "${visible}"`];
    });
    expect(mismatches).toEqual([]);
  });
});
