"use server";

import {
  provisionInputFromFormData,
  validateProvision,
  type FieldErrors,
} from "./provision-schema";

export type ProvisionState =
  { status: "idle" } | { status: "invalid"; errors: FieldErrors } | { status: "not_connected" };

/**
 * Placeholder server action for "Provision school" (FR-PLT-001).
 *
 * Validates on the server (never trust the client), then stops. Wiring (later task):
 * check the operator session + `platform.tenants.provision` with step-up MFA inside this
 * action, then POST /api/v1/platform/tenants through the BFF with an Idempotency-Key.
 * Nothing is logged here: the form contains names and email addresses.
 */
export async function provisionSchoolAction(
  _previous: ProvisionState,
  formData: FormData,
): Promise<ProvisionState> {
  const result = validateProvision(provisionInputFromFormData(formData));
  if (!result.ok) return { status: "invalid", errors: result.errors };
  return { status: "not_connected" };
}
