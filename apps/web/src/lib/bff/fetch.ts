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

/**
 * Typed browser client for the BFF (`/bff/api/v1/*`) built on @schoolos/api-client.
 *
 * - Adds X-CSRF-Token on state-changing requests (token from /bff/auth/session, memory only).
 * - Sends the UI language as Accept-Language.
 * - 401: the session has ended → go to sign-in and come back here afterwards.
 * - 428 step_up_required → go to the problem's `step_up_url` (re-authentication).
 */

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

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
    if (unsafe) {
      const token = await csrfToken(options.kind).catch(() => null);
      if (token) headers.set("x-csrf-token", token);
    }
    // Keep a copy of the body for one CSRF retry (constructing a Request consumes it).
    const spare = unsafe ? input.clone() : null;
    const request = new Request(input, { headers, credentials: "same-origin" });
    let response = await send(request);

    if (response.status === 403 && unsafe && (await readProblem(response)).code === "csrf_failed") {
      // The token may be stale (new session after step-up): re-read it once and retry.
      forgetSessionInfo(options.kind);
      const token = await csrfToken(options.kind).catch(() => null);
      if (token && spare) {
        const retry = new Request(spare, { headers, credentials: "same-origin" });
        retry.headers.set("x-csrf-token", token);
        response = await send(retry);
      }
    }

    if (response.status === 401) {
      forgetSessionInfo(options.kind);
      const target = loginUrl(options.kind, currentPath());
      navigate(target);
      throw new AuthRedirectError(target);
    }
    if (response.status === 428) {
      const problem = await readProblem(response);
      const target =
        safeStepUpUrl(problem.step_up_url) ??
        `${options.kind === "operator" ? "/bff/auth/platform/step-up" : "/bff/auth/step-up"}?next=${encodeURIComponent(currentPath())}`;
      navigate(target);
      throw new AuthRedirectError(target);
    }
    if (response.ok) reportActivity(options.kind);
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
