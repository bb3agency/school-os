// @vitest-environment node
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  AI_BUNDLE_PRICES,
  EXTRA_ANSWER_INR,
  GST_RATE_PERCENT,
  PLAN_PRICES,
  count,
  rupees,
} from "./catalogue";

/**
 * /pricing shows the published catalogue (ADR-0038, docs/18 R4). The web app never reads the
 * database, so this reads the API's versioned billing.yaml (`public_pricing`, itself pinned to
 * the catalogue rows by tests/platform/test_catalogue.py) as text and compares every number.
 */
const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../..");
const billing = readFileSync(resolve(repo, "apps/api/app/platform/billing.yaml"), "utf8").replace(
  /\r\n/g,
  "\n",
);
const start = billing.indexOf("\npublic_pricing:\n");
// The block ends at the next top-level key or comment.
const block = billing.slice(start, billing.slice(start + 1).search(/\n[^ \n]/) + start + 1);

/** `  key: {a: "1", b: 2}` → {a: "1", b: "2"} (the block's flow mappings, no nesting). */
function row(key: string): Record<string, string> {
  const match = new RegExp(`^ {4}${key}: \\{(.+)\\}$`, "m").exec(block);
  if (!match?.[1]) throw new Error(`${key} not found in billing.yaml public_pricing`);
  return Object.fromEntries(
    match[1].split(",").map((pair) => {
      const [name, value] = pair.split(":").map((part) => part.trim().replace(/^"|"$/g, ""));
      return [name as string, value as string];
    }),
  );
}

describe("the pricing page follows billing.yaml public_pricing", () => {
  it("states the plans' monthly prices and one-time fees", () => {
    for (const [code, price] of Object.entries(PLAN_PRICES)) {
      const yaml = row(code);
      expect(Number(yaml.monthly_inr), code).toBe(price.monthlyInr);
      expect(Number(yaml.one_time_fee_inr), code).toBe(price.oneTimeFeeInr);
    }
    expect([...block.matchAll(/^ {4}(shared|dedicated):/gm)]).toHaveLength(2);
  });

  it("states every AI answer bundle and the price of an extra answer", () => {
    const codes = [...block.matchAll(/^ {4}(ai-[a-z]+):/gm)].map((m) => m[1]);
    expect(Object.values(AI_BUNDLE_PRICES).map((b) => b.code)).toEqual(codes);
    for (const bundle of Object.values(AI_BUNDLE_PRICES)) {
      const yaml = row(bundle.code);
      expect(yaml.name).toBe(bundle.name);
      expect(Number(yaml.included_answers), bundle.code).toBe(bundle.includedAnswers);
      expect(Number(yaml.monthly_inr), bundle.code).toBe(bundle.monthlyInr);
      expect(Number(yaml.extra_answer_inr), bundle.code).toBe(EXTRA_ANSWER_INR);
    }
  });

  it("states the GST rate", () => {
    const match = /^ {2}gst_rate: "([\d.]+)"$/m.exec(block);
    expect(Number(match?.[1])).toBe(GST_RATE_PERCENT);
  });

  it("formats rupees in en-IN with paise only when needed", () => {
    expect(rupees(15000)).toBe("₹15,000");
    expect(rupees(4999)).toBe("₹4,999");
    expect(rupees(1.5)).toBe("₹1.50");
    expect(count(1000)).toBe("1,000");
  });
});
