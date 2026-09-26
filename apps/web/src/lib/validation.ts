import { z } from "zod";

/**
 * zod building blocks that mirror the API's Pydantic constraints (apps/api/app/platform/
 * schemas.py, identity schemas). Error messages are keys under `validation.*` (en/te).
 * The server validates again; these give instant feedback that says how to fix it.
 */

/** Same pattern as the API's `Gstin` (state code 2 digits, PAN, entity, Z, check char). */
export const GSTIN_PATTERN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
export const PAN_PATTERN = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
export const EMAIL_PATTERN = /^[^@\s]{1,64}@[a-z0-9.-]+\.[a-z]{2,63}$/;
export const DOMAIN_PATTERN = /^([a-z0-9-]{1,63}\.)+[a-z]{2,63}$/;
export const TENANT_CODE_PATTERN = /^[a-z][a-z0-9-]{1,31}$/;
export const PLAN_CODE_PATTERN = /^[a-z0-9][a-z0-9-]{1,40}$/;
export const BOARD_PATTERN = /^[A-Z][A-Z0-9_]{1,15}$/;
export const FLAG_KEY_PATTERN = /^[a-z0-9_]+(\.[a-z0-9_]+)+$/;
export const VERSION_PATTERN = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$/;
export const HOST_REF_PATTERN = /^i-[0-9a-f]{8,17}$/;
export const PAYMENT_REFERENCE_PATTERN = /^[A-Za-z0-9/_.-]{1,64}$/;
export const MONEY_PATTERN = /^\d{1,12}(\.\d{1,2})?$/;
export const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const text = (max: number, min = 1) =>
  z
    .string()
    .trim()
    .min(min, { error: min > 1 ? "tooShort" : "required" })
    .max(max, { error: "tooLong" });

export const optionalText = (max: number) =>
  z
    .string()
    .trim()
    .max(max, { error: "tooLong" })
    .transform((value) => (value === "" ? null : value));

export const email = z
  .string()
  .trim()
  .toLowerCase()
  .min(1, { error: "required" })
  .max(254, { error: "tooLong" })
  .regex(EMAIL_PATTERN, { error: "invalidEmail" });

export const optionalEmail = z
  .string()
  .trim()
  .toLowerCase()
  .max(254, { error: "tooLong" })
  .refine((value) => value === "" || EMAIL_PATTERN.test(value), { error: "invalidEmail" })
  .transform((value) => (value === "" ? null : value));

/** Reasons for suspensions, voids, offboarding… (API: 10–500 characters). */
export const reason = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(10, { error: "reasonTooShort" })
  .max(500, { error: "tooLong" });

export const stateCode = z
  .string()
  .trim()
  .regex(/^\d{2}$/, { error: "invalidStateCode" });

export const optionalGstin = z
  .string()
  .trim()
  .toUpperCase()
  .refine((value) => value === "" || GSTIN_PATTERN.test(value), { error: "invalidGstin" })
  .transform((value) => (value === "" ? null : value));

export const optionalPan = z
  .string()
  .trim()
  .toUpperCase()
  .refine((value) => value === "" || PAN_PATTERN.test(value), { error: "invalidPan" })
  .transform((value) => (value === "" ? null : value));

export const optionalDomain = z
  .string()
  .trim()
  .toLowerCase()
  .refine((value) => value === "" || (value.length >= 4 && DOMAIN_PATTERN.test(value)), {
    error: "invalidDomain",
  })
  .transform((value) => (value === "" ? null : value));

export const money = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .regex(MONEY_PATTERN, { error: "invalidAmount" });

export const optionalMoney = z
  .string()
  .trim()
  .refine((value) => value === "" || MONEY_PATTERN.test(value), { error: "invalidAmount" })
  .transform((value) => (value === "" ? null : value));

export const postalCode = z
  .string()
  .trim()
  .regex(/^[1-9][0-9]{5}$/, { error: "invalidPostalCode" });

export const optionalPhone = z
  .string()
  .trim()
  .refine((value) => value === "" || /^\+?[0-9]{10,13}$/.test(value), {
    error: "invalidPhone",
  })
  .transform((value) => (value === "" ? null : value));

export const optionalInt = (max: number) =>
  z
    .string()
    .trim()
    .refine((value) => value === "" || (/^\d+$/.test(value) && Number(value) <= max), {
      error: "invalidNumber",
    })
    .transform((value) => (value === "" ? null : Number(value)));

export const requiredInt = (min: number, max: number) =>
  z
    .string()
    .trim()
    .min(1, { error: "required" })
    .refine((value) => /^\d+$/.test(value) && Number(value) >= min && Number(value) <= max, {
      error: "invalidNumber",
    })
    .transform(Number);

export const uuid = z.string().trim().regex(UUID_PATTERN, { error: "chooseOption" });

/** `<input type="date">` value (YYYY-MM-DD). */
export const isoDate = z
  .string()
  .trim()
  .regex(/^\d{4}-\d{2}-\d{2}$/, { error: "invalidDate" });

/** `<input type="datetime-local">` (IST on screen) → RFC 3339 UTC for the API. */
export function localDateTimeToUtc(value: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const [, y, mo, d, h, mi] = match;
  // Office users work in IST (UTC+05:30) whatever the PC's time zone is set to.
  const utc = Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi)) - 330 * 60_000;
  const date = new Date(utc);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export const localDateTime = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .refine((value) => localDateTimeToUtc(value) !== null, { error: "invalidDate" })
  .transform((value) => localDateTimeToUtc(value) as string);

/** GSTIN must start with the billing state code (API model validator, GST rules). */
export function gstinMatchesState(gstin: string | null, state: string): boolean {
  return gstin === null || gstin.slice(0, 2) === state;
}

/** GSTIN characters 3–12 are the PAN. */
export function gstinMatchesPan(gstin: string | null, pan: string | null): boolean {
  return gstin === null || pan === null || gstin.slice(2, 12) === pan;
}

export const checkbox = z
  .string()
  .optional()
  .transform((value) => value === "on" || value === "true");
