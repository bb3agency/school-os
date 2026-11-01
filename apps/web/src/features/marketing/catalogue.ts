/**
 * The published starting prices on /pricing (ADR-0038, docs/18 R4), in INR before GST.
 *
 * A display copy of the commercial catalogue: invoices never read it (they use the frozen
 * `platform.plans` and `platform.ai_bundles` rows). `catalogue.test.ts` pins every number here
 * to `apps/api/app/platform/billing.yaml` → `public_pricing`, and the API test
 * `tests/platform/test_catalogue.py` pins that block to the latest published catalogue rows,
 * so a price change fails both tests until this page says the same. Messages hold no numbers:
 * every amount on the page comes from here.
 */
export const GST_RATE_PERCENT = 18;

export const PLAN_PRICES = {
  shared: { monthlyInr: 4999, oneTimeFeeInr: 15000 },
  dedicated: { monthlyInr: 9900, oneTimeFeeInr: 49000 },
} as const;

/** In the catalogue's order; codes as in `platform.ai_bundles`. */
export const AI_BUNDLE_PRICES = {
  lite: { code: "ai-lite", name: "Lite", includedAnswers: 300, monthlyInr: 699 },
  standard: { code: "ai-standard", name: "Standard", includedAnswers: 1000, monthlyInr: 1499 },
  high: { code: "ai-high", name: "High", includedAnswers: 3000, monthlyInr: 3499 },
} as const;

/** The price of each answer above a bundle's monthly quota (the same for every bundle). */
export const EXTRA_ANSWER_INR = 1.5;

/** ₹4,999 for whole rupees, ₹1.50 otherwise (en-IN grouping, Latin digits). */
export function rupees(amount: number): string {
  const whole = Number.isInteger(amount);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    numberingSystem: "latn",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  }).format(amount);
}

/** 1,000 (en-IN grouping, Latin digits). */
export function count(value: number): string {
  return new Intl.NumberFormat("en-IN", { numberingSystem: "latn" }).format(value);
}
