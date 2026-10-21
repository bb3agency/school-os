import * as z from "@/lib/zod";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { localDateTimeToUtc } from "@/lib/validation";
import { BREACH_CATEGORIES, DUTY_METHODS, isBreachCategory } from "./types";

/**
 * Client checks mirroring the API (breach_schemas.py, FR-DPDP-030): texts of 10 to 4,000
 * characters with no full Aadhaar number, 1 to 10 categories, counts from 0 to 10,00,000, and a
 * time noticed that is not in the future. Message keys are under `validation.*` (useApiForm).
 */

/** Allow a minute for a PC clock that runs a little fast. */
const CLOCK_SKEW_MS = 60_000;

/** `<input type="datetime-local">` in IST → RFC 3339 UTC, not in the future. */
export const pastDateTime = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .refine((value) => localDateTimeToUtc(value) !== null, { error: "invalidDate" })
  .transform((value) => localDateTimeToUtc(value) as string)
  .refine((iso) => new Date(iso).getTime() <= Date.now() + CLOCK_SKEW_MS, {
    error: "notInFuture",
  });

const longText = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(10, { error: "descriptionTooShort" })
  .max(4000, { error: "tooLong" })
  .refine((value) => !containsFullAadhaar(value), { error: "noAadhaar" });

const optionalLongText = z
  .string()
  .trim()
  .max(4000, { error: "tooLong" })
  .refine((value) => value === "" || value.length >= 10, { error: "descriptionTooShort" })
  .refine((value) => !containsFullAadhaar(value), { error: "noAadhaar" })
  .transform((value) => (value === "" ? null : value));

const count = z
  .string()
  .trim()
  .default("0")
  .refine((value) => value === "" || (/^\d{1,7}$/.test(value) && Number(value) <= 1_000_000), {
    error: "invalidNumber",
  })
  .transform((value) => (value === "" ? 0 : Number(value)));

const categories = z
  .array(z.string())
  .default([])
  .transform((values) => values.filter(isBreachCategory))
  .refine((values) => values.length >= 1, { error: "chooseCategories" })
  .refine((values) => values.length <= BREACH_CATEGORIES.length, { error: "chooseCategories" });

export const breachFactsSchema = z.object({
  description: longText,
  categories,
  approx_children: count,
  approx_guardians: count,
  approx_staff: count,
  containment: optionalLongText,
});

/** "Record a breach": the facts plus when the school noticed it (T0, starts every clock). */
export const newBreachSchema = breachFactsSchema.extend({ noticed_at: pastDateTime });

/** "Mark done": when (not in the future) and how the duty was done. */
export const dutyDoneSchema = z.object({
  done_at: pastDateTime,
  method: z.enum(DUTY_METHODS, { error: "chooseOption" }),
});

/** Closing with a duty not recorded needs a reason (10-500 characters, API `close_reason`). */
export function closeSchema(needsReason: boolean) {
  return z.object({
    close_reason: z
      .string()
      .trim()
      .max(500, { error: "tooLong" })
      .refine((value) => (needsReason ? value.length >= 10 : value === "" || value.length >= 10), {
        error: "reasonTooShort",
      })
      .refine((value) => !containsFullAadhaar(value), { error: "noAadhaar" })
      .transform((value) => (value === "" ? null : value)),
  });
}

/** The value for `<input type="datetime-local">` of a moment, in IST (office users' time). */
export function istLocalInput(date: Date = new Date()): string {
  const ist = new Date(date.getTime() + 330 * 60_000);
  return ist.toISOString().slice(0, 16);
}
