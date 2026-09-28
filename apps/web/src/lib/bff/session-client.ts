/**
 * Browser side of the BFF session (docs/07 §5.2). The browser only ever sees non-secret
 * facts: signed in or not, display name, CSRF token and idle expiry. Nothing here is
 * stored in localStorage/sessionStorage; the CSRF token lives in memory only.
 */

/** `support`: a SchoolOS operator using an approved break-glass grant (ADR-0023). */
export type SessionKind = "staff" | "operator" | "support";

export type SessionInfo =
  | {
      authenticated: true;
      kind: SessionKind;
      display_name: string | null;
      active_tenant_id: string | null;
      mfa: boolean;
      csrf_token: string;
      idle_timeout_ms: number;
      idle_expires_at: string;
      absolute_expires_at: string;
      expires_in_ms: number;
    }
  | { authenticated: false; kind: SessionKind };

export type Navigate = (url: string) => void;

export const defaultNavigate: Navigate = (url) => {
  window.location.assign(url);
};

/** Fired after every successful BFF call: the server slid the idle timeout. */
export const ACTIVITY_EVENT = "sos:session-activity";

export function reportActivity(kind: SessionKind): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(ACTIVITY_EVENT, { detail: { kind } }));
}

const cache = new Map<SessionKind, Promise<SessionInfo>>();

function sessionUrl(kind: SessionKind): string {
  return `/bff/auth/session?kind=${kind}`;
}

/** Session facts, fetched once per page load (pass `fresh` to re-read). */
export function loadSessionInfo(kind: SessionKind, options: { fresh?: boolean } = {}) {
  let pending = options.fresh ? undefined : cache.get(kind);
  if (!pending) {
    pending = fetch(sessionUrl(kind), {
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json" },
    }).then(async (response) => {
      if (!response.ok) throw new Error(`session info ${response.status}`);
      return (await response.json()) as SessionInfo;
    });
    pending.catch(() => cache.delete(kind));
    cache.set(kind, pending);
  }
  return pending;
}

export function forgetSessionInfo(kind?: SessionKind): void {
  if (kind) cache.delete(kind);
  else cache.clear();
}

export async function csrfToken(kind: SessionKind): Promise<string | null> {
  const info = await loadSessionInfo(kind);
  return info.authenticated ? info.csrf_token : null;
}

export function loginUrl(kind: SessionKind, next: string): string {
  // A support session starts again only from the admin panel (it needs the approved request).
  if (kind === "support") return "/signed-out?kind=support";
  const path = kind === "operator" ? "/bff/auth/platform/login" : "/bff/auth/login";
  return `${path}?next=${encodeURIComponent(next)}`;
}

export function currentPath(): string {
  if (typeof window === "undefined") return "/";
  return `${window.location.pathname}${window.location.search}`;
}

/** "Stay signed in": slide the idle timeout now. */
export async function keepAlive(kind: SessionKind): Promise<SessionInfo> {
  const token = await csrfToken(kind);
  const response = await fetch(sessionUrl(kind), {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { accept: "application/json", ...(token ? { "x-csrf-token": token } : {}) },
  });
  if (!response.ok) throw new Error(`keep alive ${response.status}`);
  const info = (await response.json()) as SessionInfo;
  cache.set(kind, Promise.resolve(info));
  return info;
}

/**
 * Make `tenantId` the active school of this staff session (POST /bff/auth/active-tenant,
 * CSRF). The BFF checks it with the API first. Resolves to null on success, otherwise
 * to the problem code (`tenant_not_available`, `csrf_failed`, …).
 */
export async function chooseSchool(tenantId: string): Promise<string | null> {
  const send = async () => {
    const token = await csrfToken("staff").catch(() => null);
    return fetch("/bff/auth/active-tenant", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
        ...(token ? { "x-csrf-token": token } : {}),
      },
      body: JSON.stringify({ tenant_id: tenantId }),
    });
  };
  let response = await send();
  if (response.status === 403) {
    const code = (
      (await response
        .clone()
        .json()
        .catch(() => ({}))) as { code?: string }
    ).code;
    if (code === "csrf_failed") {
      forgetSessionInfo("staff");
      response = await send();
    }
  }
  if (response.ok) {
    forgetSessionInfo("staff");
    return null;
  }
  const problem = (await response.json().catch(() => ({}))) as { code?: string };
  return problem.code ?? `http_${response.status}`;
}

/**
 * Sign out on the server (revokes the session and, when the IdP supports it, ends the
 * IdP session), then leave the page. Used by "Lock now" and the idle timeout.
 */
export async function signOut(kind: SessionKind, navigate: Navigate = defaultNavigate) {
  const token = await csrfToken(kind).catch(() => null);
  const response = await fetch(`/bff/auth/logout?kind=${kind}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { accept: "application/json", ...(token ? { "x-csrf-token": token } : {}) },
  });
  if (!response.ok) throw new Error(`sign out ${response.status}`);
  const { redirect_to: redirectTo } = (await response.json()) as { redirect_to: string };
  forgetSessionInfo(kind);
  navigate(redirectTo);
}
