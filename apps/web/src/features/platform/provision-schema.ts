import { z } from "zod";

/**
 * Validation for "Provision school" (FR-PLT-001). Shared by the client (instant, per-step
 * feedback) and the server action (authoritative). Error messages are message keys under
 * `platform.validation`, translated at render time.
 */
export const VALIDATION_KEYS = [
  "required",
  "tooLong",
  "invalidEmail",
  "invalidGstin",
  "invalidStateCode",
  "invalidDomain",
  "domainNeedsDedicated",
  "choosePlan",
  "chooseOption",
] as const;
export type ValidationKey = (typeof VALIDATION_KEYS)[number];

const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const HOSTNAME = /^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

const requiredText = (max: number) =>
  z.string().trim().min(1, { error: "required" }).max(max, { error: "tooLong" });

const email = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .max(254, { error: "tooLong" })
  .pipe(z.email({ error: "invalidEmail" }));

export const provisionSchoolSchema = z
  .object({
    schoolName: requiredText(200),
    legalName: requiredText(200),
    stateCode: z
      .string()
      .trim()
      .regex(/^\d{2}$/, { error: "invalidStateCode" }),
    billingEmail: email,
    gstin: z
      .string()
      .trim()
      .toUpperCase()
      .refine((value) => value === "" || GSTIN.test(value), { error: "invalidGstin" }),
    planKey: z.string().trim().min(1, { error: "choosePlan" }),
    deploymentMode: z.enum(["shared", "dedicated"], { error: "chooseOption" }),
    customDomain: z
      .string()
      .trim()
      .toLowerCase()
      .refine((value) => value === "" || HOSTNAME.test(value), { error: "invalidDomain" }),
    ownerName: requiredText(200),
    ownerEmail: email,
  })
  .superRefine((value, ctx) => {
    if (value.customDomain !== "" && value.deploymentMode !== "dedicated") {
      ctx.addIssue({ code: "custom", path: ["customDomain"], message: "domainNeedsDedicated" });
    }
  });

export type ProvisionSchoolInput = z.input<typeof provisionSchoolSchema>;
export type ProvisionSchool = z.output<typeof provisionSchoolSchema>;
export type ProvisionField = keyof ProvisionSchoolInput;
export type FieldErrors = Partial<Record<ProvisionField, ValidationKey>>;

export const PROVISION_FIELDS = [
  "schoolName",
  "legalName",
  "stateCode",
  "billingEmail",
  "gstin",
  "planKey",
  "deploymentMode",
  "customDomain",
  "ownerName",
  "ownerEmail",
] as const satisfies readonly ProvisionField[];

export const PROVISION_STEPS = ["school", "plan", "owner", "review"] as const;
export type ProvisionStep = (typeof PROVISION_STEPS)[number];

export const STEP_FIELDS: Record<ProvisionStep, readonly ProvisionField[]> = {
  school: ["schoolName", "legalName", "stateCode", "billingEmail", "gstin"],
  plan: ["planKey", "deploymentMode", "customDomain"],
  owner: ["ownerName", "ownerEmail"],
  review: [],
};

function isValidationKey(value: string): value is ValidationKey {
  return (VALIDATION_KEYS as readonly string[]).includes(value);
}

function isProvisionField(value: unknown): value is ProvisionField {
  return typeof value === "string" && (PROVISION_FIELDS as readonly string[]).includes(value);
}

/** Read the form's fields as strings (missing fields become ""). */
export function provisionInputFromFormData(formData: FormData): Record<ProvisionField, string> {
  const entries = PROVISION_FIELDS.map((field) => {
    const value = formData.get(field);
    return [field, typeof value === "string" ? value : ""] as const;
  });
  return Object.fromEntries(entries) as Record<ProvisionField, string>;
}

export type ProvisionValidation =
  { ok: true; data: ProvisionSchool } | { ok: false; errors: FieldErrors };

export function validateProvision(input: Record<ProvisionField, string>): ProvisionValidation {
  const result = provisionSchoolSchema.safeParse(input);
  if (result.success) return { ok: true, data: result.data };
  const errors: FieldErrors = {};
  for (const issue of result.error.issues) {
    const field = issue.path[0];
    if (!isProvisionField(field) || errors[field]) continue;
    errors[field] = isValidationKey(issue.message) ? issue.message : "required";
  }
  return { ok: false, errors };
}
