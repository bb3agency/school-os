import { AuthRedirectError } from "@/lib/bff/fetch";
import { ApiError, NotAvailableError } from "@/lib/bff/query";
import { StepUpCancelledError } from "@/lib/bff/step-up";

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
  "step_up_cancelled",
  // School provisioning (FR-PLT-002, docs/16 §5.4): resume and go-live refusals.
  "provisioning_in_progress",
  "provisioning_incomplete",
  "provisioning_failed",
  "resume_needs_request",
  // Academic structure (US-202, FR-TEN-010): archived rows and rows still in use.
  "structure_archived",
  "structure_in_use",
  // Offboarding (FR-PLT-005, docs/16 §5.5.1): export gate, dedicated teardown, certificate.
  "export_already_confirmed",
  "not_dedicated",
  "not_offboarding",
  "certificate_pending",
] as const;
export type KnownApiCode = (typeof KNOWN_API_CODES)[number];

export type ApiErrorKind =
  | { kind: "redirecting" }
  | { kind: "unavailable" }
  | {
      kind: "api";
      key: KnownApiCode | "generic";
      status: number;
      requestId: string | null;
      /** Seconds to wait from a 429 (`retry_after` in the problem, RFC 9110 Retry-After). */
      retryAfter?: number;
    };

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
  // The user closed "confirm it's you": nothing was done (ADR-0018).
  if (error instanceof StepUpCancelledError) {
    return { kind: "api", key: "step_up_cancelled", status: 428, requestId: null };
  }
  if (error instanceof ApiError) {
    const key = isKnown(error.code) ? error.code : (STATUS_FALLBACK[error.status] ?? "generic");
    const retry = (error.problem as { retry_after?: unknown }).retry_after;
    return {
      kind: "api",
      key,
      status: error.status,
      requestId: typeof error.problem.request_id === "string" ? error.problem.request_id : null,
      ...(error.status === 429 && typeof retry === "number" && Number.isInteger(retry) && retry > 0
        ? { retryAfter: retry }
        : {}),
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
