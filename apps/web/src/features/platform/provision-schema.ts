import type { ProvisionRequest } from "@schoolos/api-client";
import { z } from "zod";
import {
  BOARD_PATTERN,
  TENANT_CODE_PATTERN,
  optionalDomain,
  optionalEmail,
  optionalMoney,
  text,
  uuid,
} from "@/lib/validation";
import { BILLING_FIELDS, billingShape, checkBilling, toBillingAccount } from "./billing-account";

/**
 * "Provision school" (FR-PLT-001..003, docs/16 §5.4), mirroring the API's ProvisionIn.
 * Input names are the API's body paths (`owner.email`, `billing_account.gstin`) so server
 * field errors land on the right input. Messages are keys under `validation.*`.
 */

export const PROVISION_STEPS = [
  "school",
  "deployment",
  "owner",
  "plan",
  "billing",
  "review",
] as const;
export type ProvisionStep = (typeof PROVISION_STEPS)[number];

const BA = "billing_account.";

export const STEP_FIELDS: Record<ProvisionStep, readonly string[]> = {
  school: ["school_name", "code", "boards"],
  deployment: ["tier", "custom_domain"],
  owner: ["owner.display_name", "owner.email", "owner.idp_subject", "owner.language"],
  plan: ["plan_id", "start_as", "price_override_inr", "override_reason"],
  billing: BILLING_FIELDS.map((field) => `${BA}${field}`),
  review: [],
};

const boards = z
  .string()
  .trim()
  .transform((value) =>
    value === ""
      ? []
      : value
          .split(/[,\s]+/)
          .filter(Boolean)
          .map((board) => board.toUpperCase()),
  )
  .refine((list) => list.length <= 8 && list.every((board) => BOARD_PATTERN.test(board)), {
    error: "invalidBoards",
  });

const base = z.object({
  school_name: text(200),
  code: z
    .string()
    .trim()
    .toLowerCase()
    .min(1, { error: "required" })
    .regex(TENANT_CODE_PATTERN, { error: "invalidCode" }),
  boards,
  tier: z.enum(["shared", "dedicated"], { error: "chooseOption" }),
  custom_domain: optionalDomain,
  "owner.display_name": z.string().trim().max(200, { error: "tooLong" }),
  "owner.email": optionalEmail,
  "owner.idp_subject": z
    .string()
    .trim()
    .max(255, { error: "tooLong" })
    .refine((value) => !/\s/.test(value), { error: "noSpaces" }),
  "owner.language": z.enum(["en", "te"], { error: "chooseOption" }),
  plan_id: z.string().trim().min(1, { error: "choosePlan" }).pipe(uuid),
  start_as: z.enum(["trial", "active"], { error: "chooseOption" }),
  price_override_inr: optionalMoney,
  override_reason: z
    .string()
    .trim()
    .max(500, { error: "tooLong" })
    .transform((value) => (value === "" ? null : value)),
});

export const provisionSchema = base
  .extend(billingShape(BA))
  .superRefine((value, ctx) => {
    checkBilling(BA, value, ctx);
    const ownerGiven =
      value["owner.display_name"] !== "" ||
      value["owner.idp_subject"] !== "" ||
      value["owner.email"] !== null;
    if (value.tier === "shared" || ownerGiven) {
      if (value["owner.display_name"] === "") {
        ctx.addIssue({ code: "custom", path: ["owner.display_name"], message: "required" });
      }
      if (value["owner.idp_subject"] === "") {
        ctx.addIssue({ code: "custom", path: ["owner.idp_subject"], message: "required" });
      }
    }
    if (value.custom_domain !== null && value.tier !== "dedicated") {
      ctx.addIssue({ code: "custom", path: ["custom_domain"], message: "domainNeedsDedicated" });
    }
    if ((value.price_override_inr === null) !== (value.override_reason === null)) {
      ctx.addIssue({
        code: "custom",
        path: [value.price_override_inr === null ? "price_override_inr" : "override_reason"],
        message: "priceNeedsReason",
      });
    } else if (value.override_reason !== null && value.override_reason.length < 10) {
      ctx.addIssue({ code: "custom", path: ["override_reason"], message: "reasonTooShort" });
    }
  })
  .transform((value): ProvisionRequest => ({
    code: value.code,
    school_name: value.school_name,
    boards: value.boards,
    tier: value.tier,
    custom_domain: value.custom_domain,
    plan_id: value.plan_id,
    start_as: value.start_as,
    price_override_inr: value.price_override_inr,
    override_reason: value.override_reason,
    owner:
      value["owner.display_name"] !== "" && value["owner.idp_subject"] !== ""
        ? {
            display_name: value["owner.display_name"],
            email: value["owner.email"],
            idp_subject: value["owner.idp_subject"],
            language: value["owner.language"],
          }
        : null,
    billing_account: toBillingAccount(BA, value),
  }));

/** Client errors of one step only (per-step "Next"). */
export function stepErrors(
  step: ProvisionStep,
  all: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(all).filter(([field]) => STEP_FIELDS[step].includes(field)),
  );
}

/** The first step holding any of these fields (server or client errors). */
export function firstStepWith(fields: readonly string[]): ProvisionStep | null {
  for (const step of PROVISION_STEPS) {
    if (fields.some((field) => STEP_FIELDS[step].includes(field))) return step;
  }
  return null;
}
