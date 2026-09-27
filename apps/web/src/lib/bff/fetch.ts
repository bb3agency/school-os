import {
  createApiClient,
  type ApiClient,
  type FetchLike,
  type Problem,
} from "@schoolos/api-client";
import {
  csrfToken,
  currentPath,
  defaultNavigate,
  forgetSessionInfo,
  loginUrl,
  reportActivity,
  type Navigate,
  type SessionKind,
} from "./session-client";
import { StepUpCancelledError, stepUpHandler } from "./step-up";

/**
 * Typed browser client for the BFF (`/bff/api/v1/*`) built on @schoolos/api-client.
 *
 * - Adds X-CSRF-Token on state-changing requests (token from /bff/auth/session, memory only).
 * - Sends the UI language as Accept-Language.
 * - 401: the session has ended → go to sign-in and come back here afterwards.
 * - 428 step_up_required → the page's step-up handler (StepUpHost) confirms the user in a
 *   separate window and the request is sent once more (ADR-0018); without a handler, or if the
 *   API still answers 428, go to the problem's `step_up_url` (full-page re-authentication).
 * - 403 tenant_suspended → tell the page (TENANT_SUSPENDED_EVENT) so the suspended-school
 *   banner can explain it, whatever screen made the call (BR-08).
 * - Passive calls (PASSIVE_HEADER, e.g. the notification bell's polling) do not count as
 *   activity: the BFF does not slide the idle timeout, and a 401 or 428 does not leave the page.
 */

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Request header for background polls. The BFF then reads the session without sliding the
 * idle timeout (an unattended office PC still locks after 15 minutes), and this client neither
 * reports activity nor navigates to sign-in on 401. GET only.
 */
export const PASSIVE_HEADER = "x-sos-passive";

/** Window event fired when the API answers 403 tenant_suspended (BR-08, FR-PLT-004). */
export const TENANT_SUSPENDED_EVENT = "sos:tenant-suspended";

function reportTenantSuspended(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(TENANT_SUSPENDED_EVENT));
}

/** Thrown while the page navigates away to sign in or step up. */
export class AuthRedirectError extends Error {
  constructor(readonly target: string) {
    super("redirecting to sign-in");
    this.name = "AuthRedirectError";
  }
}

export interface BffFetchOptions {
  kind: SessionKind;
  locale: string;
  navigate?: Navigate;
  fetchImpl?: (request: Request) => Promise<Response>;
}

async function readProblem(
  response: Response,
): Promise<Partial<Problem> & { step_up_url?: string }> {
  try {
    return (await response.clone().json()) as Partial<Problem> & { step_up_url?: string };
  } catch {
    return {};
  }
}

function safeStepUpUrl(value: unknown): string | null {
  return typeof value === "string" && /^\/bff\/auth\/(platform\/)?step-up(\?|$)/.test(value)
    ? value
    : null;
}

export function createBffFetch(options: BffFetchOptions): FetchLike {
  const navigate = options.navigate ?? defaultNavigate;
  const send = options.fetchImpl ?? ((request: Request) => fetch(request));

  return async (input) => {
    const headers = new Headers(input.headers);
    headers.set("accept-language", options.locale);
    const unsafe = UNSAFE.has(input.method.toUpperCase());
    const passive = !unsafe && headers.get(PASSIVE_HEADER) === "1";
    if (unsafe) {
      const token = await csrfToken(options.kind).catch(() => null);
      if (token) headers.set("x-csrf-token", token);
    }
    // Keep a copy of the body for the retries below (constructing a Request consumes it).
    const spare = input.clone();
    const again = async (): Promise<Response> => {
      const retry = new Request(spare.clone(), { headers, credentials: "same-origin" });
      if (unsafe) {
        const token = await csrfToken(options.kind).catch(() => null);
        if (token) retry.headers.set("x-csrf-token", token);
      }
      return send(retry);
    };
    let response = await send(new Request(input, { headers, credentials: "same-origin" }));

    if (response.status === 403 && unsafe && (await readProblem(response)).code === "csrf_failed") {
      // The token may be stale (new session after step-up): re-read it once and retry.
      forgetSessionInfo(options.kind);
      response = await again();
    }

    if (response.status === 428 && !passive) {
      const problem = await readProblem(response);
      const target =
        safeStepUpUrl(problem.step_up_url) ??
        `${options.kind === "operator" ? "/bff/auth/platform/step-up" : "/bff/auth/step-up"}?next=${encodeURIComponent(currentPath())}`;
      const handler = stepUpHandler(options.kind);
      if (handler) {
        // Confirm it's you in a separate window, then send the same request once more. The
        // form on this page (and whatever was typed) stays as it is (ADR-0018).
        if (!(await handler(target))) throw new StepUpCancelledError();
        forgetSessionInfo(options.kind);
        response = await again();
      }
      if (response.status === 428) {
        navigate(target);
        throw new AuthRedirectError(target);
      }
    }

    if (response.status === 403 && (await readProblem(response)).code === "tenant_suspended") {
      reportTenantSuspended();
    }
    if (response.status === 401) {
      forgetSessionInfo(options.kind);
      // A background poll never takes the user away: the idle-timeout dialog handles that.
      if (passive) return response;
      const target = loginUrl(options.kind, currentPath());
      navigate(target);
      throw new AuthRedirectError(target);
    }
    if (response.ok && !passive) reportActivity(options.kind);
    return response;
  };
}

/** Absolute base so it also works where relative URLs are not allowed (tests). */
function bffBase(): string {
  return typeof window === "undefined" ? "http://localhost/bff" : `${window.location.origin}/bff`;
}

export function createBffClient(options: BffFetchOptions): ApiClient {
  return createApiClient(bffBase(), createBffFetch(options));
}
