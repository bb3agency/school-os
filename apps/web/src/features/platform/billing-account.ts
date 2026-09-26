import type { BillingAccountInput } from "@schoolos/api-client";
import { z } from "zod";
import {
  email,
  gstinMatchesPan,
  gstinMatchesState,
  optionalGstin,
  optionalPan,
  optionalPhone,
  optionalText,
  postalCode,
  stateCode,
  text,
} from "@/lib/validation";

/**
 * Billing account fields (FR-PLT-015, docs/16 §5.8), mirroring the API's BillingAccountIn:
 * GSTIN format, its first two digits = state code, characters 3–12 = PAN when both given.
 * `prefix` lets the provision wizard name its inputs `billing_account.<field>` so server
 * errors (`field: "billing_account.gstin"`) land on the right input.
 */
export const BILLING_FIELDS = [
  "legal_name",
  "gstin",
  "pan",
  "billing_email",
  "billing_contact_name",
  "billing_phone",
  "address_line1",
  "address_line2",
  "city",
  "district",
  "postal_code",
  "state_code",
  "po_reference",
] as const;
export type BillingField = (typeof BILLING_FIELDS)[number];

const fieldSchemas = {
  legal_name: text(200),
  gstin: optionalGstin,
  pan: optionalPan,
  billing_email: email,
  billing_contact_name: optionalText(200),
  billing_phone: optionalPhone,
  address_line1: text(200),
  address_line2: optionalText(200),
  city: text(100),
  district: optionalText(100),
  postal_code: postalCode,
  state_code: stateCode,
  po_reference: optionalText(100),
} as const;

type FieldSchemas = typeof fieldSchemas;
export type BillingShape<P extends string> = {
  [K in BillingField as `${P}${K}`]: FieldSchemas[K];
};

export function billingShape<P extends string = "">(prefix: P = "" as P): BillingShape<P> {
  return Object.fromEntries(
    BILLING_FIELDS.map((field) => [`${prefix}${field}`, fieldSchemas[field]]),
  ) as BillingShape<P>;
}

/** Cross-field GST rules (API model validator). */
export function checkBilling(prefix: string, value: Record<string, unknown>, ctx: z.RefinementCtx) {
  const gstin = (value[`${prefix}gstin`] ?? null) as string | null;
  const state = String(value[`${prefix}state_code`] ?? "");
  const pan = (value[`${prefix}pan`] ?? null) as string | null;
  if (!gstinMatchesState(gstin, state)) {
    ctx.addIssue({ code: "custom", path: [`${prefix}gstin`], message: "gstinStateMismatch" });
  }
  if (!gstinMatchesPan(gstin, pan)) {
    ctx.addIssue({ code: "custom", path: [`${prefix}pan`], message: "gstinPanMismatch" });
  }
}

export function toBillingAccount(prefix: string, value: Record<string, unknown>) {
  return Object.fromEntries(
    BILLING_FIELDS.map((field) => [field, value[`${prefix}${field}`]]),
  ) as unknown as BillingAccountInput;
}

export function billingAccountSchema<P extends string = "">(prefix: P = "" as P) {
  return z
    .object(billingShape(prefix))
    .superRefine((value, ctx) => checkBilling(prefix, value, ctx))
    .transform((value) => toBillingAccount(prefix, value));
}
