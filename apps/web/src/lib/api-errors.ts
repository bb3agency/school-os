import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError } from "@/lib/bff/query";

/**
 * Turn an error from a BFF call into message keys the UI translates (en/te). Screens never
 * show raw API text: every known `code` has a plain-language message that says how to fix
 * it; anything else falls back to a generic message plus the request id for support.
 */

/** API/BFF problem codes with their own message under `errors.api.*`. */
export const KNOWN_API_CODES = [
  "same_operator",
  "invalid_state",
  "duplicate",
  "conflict",
  "precondition_failed",
  "validation_error",
  "forbidden",
  "not_found",
  "plan_published",
  "idempotency_in_progress",
  "exam_window",
  "grace_not_over",
  "last_owner",
  "own_account",
  "own_roles",
  "owner_exists",
  "empty_invoice",
  "invoice_issued",
  "overdue_invoices",
  "ticket_closed",
  "tier_change",
  "shared_tier",
  "already_requested",
  "not_requested",
  "billing_suspension",
  "mfa_required",
  "tenant_suspended",
  "csrf_failed",
  "payload_too_large",
  "rate_limited",
  "bad_gateway",
  "service_unavailable",
  "tenant_not_available",
] as const;
export type KnownApiCode = (typeof KNOWN_API_CODES)[number];

export type ApiErrorKind =
  | { kind: "redirecting" }
  | { kind: "unavailable" }
  | { kind: "api"; key: KnownApiCode | "generic"; status: number; requestId: string | null };

const STATUS_FALLBACK: Record<number, KnownApiCode> = {
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  412: "precondition_failed",
  413: "payload_too_large",
  422: "validation_error",
  429: "rate_limited",
  502: "bad_gateway",
  503: "service_unavailable",
};

function isKnown(code: string | undefined): code is KnownApiCode {
  return code !== undefined && (KNOWN_API_CODES as readonly string[]).includes(code);
}

export function describeApiError(error: unknown): ApiErrorKind {
  if (error instanceof AuthRedirectError) return { kind: "redirecting" };
  if (error instanceof NotAvailableError) return { kind: "unavailable" };
  if (error instanceof ApiError) {
    const key = isKnown(error.code) ? error.code : (STATUS_FALLBACK[error.status] ?? "generic");
    return {
      kind: "api",
      key,
      status: error.status,
      requestId: typeof error.problem.request_id === "string" ? error.problem.request_id : null,
    };
  }
  // fetch() itself failed: offline or the server is down.
  return { kind: "api", key: "bad_gateway", status: 0, requestId: null };
}

/** Field errors from a 422 problem: `field` (dotted body path) → message key suffix. */
export function apiFieldErrors(error: unknown): Array<{ field: string; key: string }> {
  if (!(error instanceof ApiError)) return [];
  const errors = error.problem.errors;
  if (!Array.isArray(errors)) return [];
  return errors
    .filter((item) => typeof item?.field === "string")
    .map((item) => ({
      field: item.field,
      key:
        typeof item.message_key === "string"
          ? item.message_key.replace(/^errors\./, "")
          : "invalid",
    }));
}
