import "server-only";
import * as oauth from "openid-client";
import { ENGLISH, TELUGU, uiLocale } from "@/i18n/languages";
import type { Locale } from "@/i18n/routing";
import { requestIdFrom } from "@/lib/problem";
import {
  csrfFailed,
  csrfOk,
  jsonResponse,
  kindParam,
  problem,
  readSchoolSession,
  readSession,
  seeOther,
} from "@/server/bff/http";
import { callApi } from "@/server/bff/upstream";
import { signedOutPath, type SessionKind } from "@/server/config";
import { logEvent } from "@/server/log";
import type { AuthRuntime } from "@/server/runtime";
import {
  clearCookie,
  parseCookies,
  serializeCookie,
  sessionCookieName,
  transactionCookieName,
} from "@/server/session/cookies";
import { schoolIdleTimeoutFromMe } from "@/server/session/school-settings";
import type { Session } from "@/server/session/store";
import { sessionClaims } from "./oidc";
import { DEFAULT_NEXT, safeNext } from "./redirect";
import { SessionEndedError } from "./refresh";
import { TRANSACTION_TTL_SECONDS } from "./transaction";

/**
 * BFF authentication route handlers (FR-IAM-001, FR-IAM-003, FR-IAM-004, SEC-004,
 * SEC-005; docs/07 §5; ADR-0018). Framework-free: each takes a Request and the runtime,
 * returns a Response. Tokens never leave the server.
 *
 * Break-glass support sign-in (ADR-0023 option C; SEC-021): an operator opens an approved
 * grant from the admin panel (`/bff/auth/support/login?request=&tenant=`). The BFF runs
 * Authorization Code + PKCE against the support app client of the operator pool with a forced
 * fresh sign-in, keeps the tokens in its own `__Host-sos_support_session` session, and starts
 * the support session with the API (`POST /api/v1/breakglass/support-session`, audited in the
 * school's and the control-plane chains) before the operator sees any school page.
 */

/** Step-up must be a sign-in within the last 5 minutes (docs/07 §5.2). */
const STEP_UP_MAX_AGE_SECONDS = 5 * 60;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_SMALL_BODY = 4 * 1024;

export type SignInError =
  | "signin_expired"
  | "signin_failed"
  | "signin_unavailable"
  | "mfa_required"
  | "step_up_failed"
  | "support_not_allowed"
  | "support_ended";

function signedOutUrl(kind: SessionKind, error?: SignInError): string {
  const base = signedOutPath(kind);
  return error ? `${base}${base.includes("?") ? "&" : "?"}error=${error}` : base;
}

function platformDisabled(requestId: string): Response {
  return problem(requestId, 404, "not_found", "Not found");
}

/** Operator routes need the control plane on this host; support routes the support client. */
function kindDisabled(runtime: AuthRuntime, kind: SessionKind): boolean {
  if (kind === "operator") return !runtime.config.platformEnabled;
  if (kind === "support") return !runtime.config.supportEnabled;
  return false;
}

/** Redirect the browser to the IdP with a new sign-in transaction (PKCE S256, state, nonce). */
async function startSignIn(
  runtime: AuthRuntime,
  kind: SessionKind,
  next: string,
  stepUpSessionId: string | null,
  support?: { requestId: string; tenantId: string },
): Promise<Response> {
  const state = oauth.randomState();
  const nonce = oauth.randomNonce();
  const codeVerifier = oauth.randomPKCECodeVerifier();
  let authorizationUrl: URL;
  try {
    authorizationUrl = await runtime.oidc[kind].authorizationUrl({
      state,
      nonce,
      codeChallenge: await oauth.calculatePKCECodeChallenge(codeVerifier),
      // Support sign-in always re-authenticates: step-up at session start (ADR-0023 §4).
      stepUp: stepUpSessionId !== null || support !== undefined,
    });
  } catch (error) {
    logEvent(
      "oidc_discovery_failed",
      { kind, code: error instanceof Error ? error.name : "unknown" },
      "error",
    );
    return seeOther(runtime, signedOutUrl(kind, "signin_unavailable"));
  }
  const secure = runtime.config.secureCookies;
  const transaction = runtime.transactions.encode({
    kind,
    state,
    nonce,
    codeVerifier,
    next,
    stepUpSessionId,
    ...(support ? { support } : {}),
  });
  return seeOther(runtime, authorizationUrl.href, [
    serializeCookie(transactionCookieName(kind, secure), transaction, {
      secure,
      maxAge: TRANSACTION_TTL_SECONDS,
    }),
  ]);
}

/** GET /bff/auth/login?next= and /bff/auth/platform/login?next= */
export async function handleLogin(request: Request, runtime: AuthRuntime, kind: SessionKind) {
  // Support sessions start only from a grant: GET /bff/auth/support/login.
  if (kind === "support" || kindDisabled(runtime, kind)) {
    return platformDisabled(requestIdFrom(request.headers));
  }
  const next = safeNext(new URL(request.url).searchParams.get("next"), kind);
  if (await readSession(request, runtime, kind, { touch: false })) return seeOther(runtime, next);
  return startSignIn(runtime, kind, next, null);
}

/** GET /bff/auth/step-up?next= : re-authenticate now (prompt=login, max_age=0; ADR-0018). */
export async function handleStepUp(request: Request, runtime: AuthRuntime, kind: SessionKind) {
  if (kindDisabled(runtime, kind)) {
    return platformDisabled(requestIdFrom(request.headers));
  }
  const next = safeNext(new URL(request.url).searchParams.get("next"), kind);
  const current = await readSession(request, runtime, kind, { touch: false });
  return startSignIn(runtime, kind, next, current?.session.id ?? null);
}

/** One JSON call from the BFF to the API on the user's behalf (sign-in helpers). */
async function apiCall(
  runtime: AuthRuntime,
  session: Session,
  accessToken: string,
  method: "GET" | "POST",
  path: string,
  requestId: string,
  payload: unknown = {},
): Promise<{ status: number; body: unknown }> {
  const response = await callApi(runtime, {
    session,
    accessToken,
    method,
    path,
    incomingHeaders: new Headers({ "content-type": "application/json" }),
    body: method === "POST" ? new TextEncoder().encode(JSON.stringify(payload)) : null,
    requestId,
    headersTimeoutMs: 5_000,
  });
  let body: unknown = null;
  if ((response.headers.get("content-type") ?? "").includes("json")) {
    body = await response.json().catch(() => null);
  } else {
    await response.body?.cancel();
  }
  return { status: response.status, body };
}

function problemCodeOf(body: unknown): string | null {
  return body && typeof body === "object" && typeof (body as { code?: unknown }).code === "string"
    ? (body as { code: string }).code
    : null;
}

/**
 * Best-effort audit of the sign-in by the API (`auth.login.succeeded` / `denied` in the
 * school's log). Sent with X-Active-Tenant when the school is known.
 */
async function recordLoginEvent(
  runtime: AuthRuntime,
  session: Session,
  accessToken: string,
  requestId: string,
): Promise<void> {
  try {
    const { status } = await apiCall(
      runtime,
      session,
      accessToken,
      "POST",
      "/api/v1/me/login-event",
      requestId,
    );
    if (status >= 400 && status !== 404) {
      logEvent("login_event_not_recorded", { status, request_id: requestId });
    }
  } catch {
    logEvent("login_event_not_recorded", { code: "network", request_id: requestId });
  }
}

interface SchoolChoice {
  tenant_id: string;
  status: string;
}

function parseSchools(body: unknown): SchoolChoice[] | null {
  const data = body && typeof body === "object" ? (body as { data?: unknown }).data : undefined;
  if (!Array.isArray(data)) return null;
  return data.filter(
    (item): item is SchoolChoice =>
      item !== null &&
      typeof item === "object" &&
      typeof (item as SchoolChoice).tenant_id === "string" &&
      UUID.test((item as SchoolChoice).tenant_id) &&
      typeof (item as SchoolChoice).status === "string",
  );
}

type StaffSignIn =
  | { kind: "mfa_required" }
  | { kind: "single"; tenantId: string }
  | { kind: "choose" }
  | { kind: "none" }
  | { kind: "unknown" };

/**
 * After a staff sign-in (ADR-0019, FR-IAM-013): accept pending invitations FIRST, then list
 * the user's schools. One usable school becomes the active school; several (or one that is
 * suspended) go to the picker; none shows "no access yet". The login event is audited once
 * the school is known (here, or when the user picks one).
 */
async function afterStaffSignIn(
  runtime: AuthRuntime,
  session: Session,
  accessToken: string,
  requestId: string,
): Promise<StaffSignIn> {
  try {
    const accepted = await apiCall(
      runtime,
      session,
      accessToken,
      "POST",
      "/api/v1/me/accept-invitations",
      requestId,
    );
    if (accepted.status === 403 && problemCodeOf(accepted.body) === "mfa_required") {
      return { kind: "mfa_required" };
    }
    if (accepted.status >= 400) {
      logEvent("invitations_not_accepted", { status: accepted.status, request_id: requestId });
    }
  } catch {
    logEvent("invitations_not_accepted", { code: "network", request_id: requestId });
  }

  let schools: SchoolChoice[] | null = null;
  try {
    const listed = await apiCall(
      runtime,
      session,
      accessToken,
      "GET",
      "/api/v1/me/schools",
      requestId,
    );
    if (listed.status === 403 && problemCodeOf(listed.body) === "mfa_required") {
      return { kind: "mfa_required" };
    }
    if (listed.status === 200) schools = parseSchools(listed.body);
    else logEvent("schools_not_listed", { status: listed.status, request_id: requestId });
  } catch {
    logEvent("schools_not_listed", { code: "network", request_id: requestId });
  }

  if (schools === null) {
    // A hiccup: sign in as before; the school pages ask for a school if they need one.
    await recordLoginEvent(runtime, session, accessToken, requestId);
    return { kind: "unknown" };
  }
  if (schools.length === 0) return { kind: "none" };
  const only = schools.length === 1 ? schools[0] : undefined;
  if (only) {
    const tenantId = only.tenant_id.toLowerCase();
    // Audited either way: the API records a denial for a suspended school.
    await recordLoginEvent(
      runtime,
      { ...session, activeTenantId: tenantId },
      accessToken,
      requestId,
    );
    if (only.status === "active") {
      await runtime.store.setActiveTenant(session, tenantId);
      return { kind: "single", tenantId };
    }
    return { kind: "choose" };
  }
  await runtime.store.setActiveTenant(session, null, { loginEventPending: true });
  return { kind: "choose" };
}

/**
 * Start the support session with the API (ADR-0023 §4): the API checks the grant, the school
 * and a fresh sign-in, and audits `breakglass.session_started`. Anything but 200 refuses.
 */
async function startSupportSession(
  runtime: AuthRuntime,
  session: Session,
  accessToken: string,
  platformRequestId: string,
  requestId: string,
): Promise<"ok" | SignInError> {
  try {
    const { status } = await apiCall(
      runtime,
      session,
      accessToken,
      "POST",
      "/api/v1/breakglass/support-session",
      requestId,
      { platform_request_id: platformRequestId },
    );
    if (status === 200) return "ok";
    logEvent("support_session_refused", { status, request_id: requestId });
    if (status === 409) return "support_ended";
    if (status === 428) return "step_up_failed";
    if ([401, 403, 404].includes(status)) return "support_not_allowed";
    return "signin_unavailable";
  } catch {
    logEvent("support_session_refused", { code: "network", request_id: requestId });
    return "signin_unavailable";
  }
}

/**
 * GET /bff/auth/support/login?request=<control-plane request id>&tenant=<school id>
 * (ADR-0023): start a fresh, MFA sign-in with the support client for one approved grant.
 *
 * The admin panel may live on another host (`admin.<domain>`), and the sign-in cookies are
 * host-only (`__Host-`), so the first request is always sent once to the school app's own
 * address (APP_BASE_URL, never the Host header) with `c=1`; the sign-in starts there.
 */
export async function handleSupportLogin(request: Request, runtime: AuthRuntime) {
  if (kindDisabled(runtime, "support")) return platformDisabled(requestIdFrom(request.headers));
  const params = new URL(request.url).searchParams;
  const platformRequestId = params.get("request") ?? "";
  const tenantId = params.get("tenant") ?? "";
  if (!UUID.test(platformRequestId) || !UUID.test(tenantId)) {
    return seeOther(runtime, signedOutUrl("support", "support_not_allowed"));
  }
  if (params.get("c") !== "1") {
    const canonical = new URLSearchParams({
      request: platformRequestId.toLowerCase(),
      tenant: tenantId.toLowerCase(),
      c: "1",
    });
    return seeOther(runtime, `/bff/auth/support/login?${canonical.toString()}`);
  }
  const next = safeNext(params.get("next"), "support");
  return startSignIn(runtime, "support", next, null, {
    requestId: platformRequestId.toLowerCase(),
    tenantId: tenantId.toLowerCase(),
  });
}

/**
 * The UI language of a `next` path (`/te/...` while Telugu is switched on), English otherwise
 * (ADR-0036: with Telugu off the proxy sends `/te` pages to `/en` anyway).
 */
function localeOf(path: string): Locale {
  return /^\/te(\/|$|\?)/.test(path) ? uiLocale(TELUGU) : ENGLISH;
}

async function revokeAtIdp(runtime: AuthRuntime, session: Session): Promise<void> {
  try {
    const stored = await runtime.store.tokens(session.id);
    if (stored?.tokens.refreshToken) {
      await runtime.oidc[session.kind].revoke(stored.tokens.refreshToken);
    }
  } catch {
    logEvent("idp_revocation_failed", { kind: session.kind });
  }
}

/** GET /bff/auth/callback and /bff/auth/platform/callback */
export async function handleCallback(request: Request, runtime: AuthRuntime, kind: SessionKind) {
  const requestId = requestIdFrom(request.headers);
  if (kindDisabled(runtime, kind)) return platformDisabled(requestId);
  const { config, store } = runtime;
  const secure = config.secureCookies;
  const cookies = parseCookies(request.headers.get("cookie"));
  const clearTransaction = clearCookie(transactionCookieName(kind, secure), secure);
  const fail = (error: SignInError) => {
    logEvent("signin_failed", { kind, code: error, request_id: requestId });
    return seeOther(runtime, signedOutUrl(kind, error), [clearTransaction]);
  };

  const transaction = runtime.transactions.decode(
    cookies.get(transactionCookieName(kind, secure)),
    kind,
  );
  if (!transaction) return fail("signin_expired");

  // The IdP redirected to our registered callback; rebuild it from APP_BASE_URL, not Host.
  const incoming = new URL(request.url);
  const callbackUrl = new URL(`${incoming.pathname}${incoming.search}`, config.appBaseUrl);
  let tokens;
  try {
    tokens = await runtime.oidc[kind].exchangeCode(callbackUrl, {
      state: transaction.state,
      nonce: transaction.nonce,
      codeVerifier: transaction.codeVerifier,
    });
  } catch (error) {
    logEvent("oidc_callback_rejected", {
      kind,
      code: error instanceof Error ? error.name : "unknown",
      request_id: requestId,
    });
    return fail("signin_failed");
  }
  const claims = sessionClaims(tokens);
  if (!claims) return fail("signin_failed");
  // Operators always need MFA (contract §5), also as SchoolOS support (ADR-0023); the API
  // checks it again on every request.
  if ((kind === "operator" || kind === "support") && !claims.mfa) return fail("mfa_required");

  const nowSeconds = Math.floor(runtime.now() / 1000);
  const support = kind === "support" && !transaction.stepUpSessionId ? transaction.support : null;
  if (kind === "support" && !transaction.stepUpSessionId) {
    // A support session starts only for a grant, with a sign-in within 5 minutes (step-up).
    if (!support) return fail("support_not_allowed");
    if (claims.authTime === null || nowSeconds - claims.authTime > STEP_UP_MAX_AGE_SECONDS) {
      return fail("step_up_failed");
    }
  }
  const existing = await store.load(cookies.get(sessionCookieName(kind, secure)), {
    touch: false,
  });
  let carried: Partial<
    Pick<Session, "familyId" | "handle" | "csrfToken" | "activeTenantId" | "idleTimeoutMs">
  > = {};
  let next = transaction.next;
  if (transaction.stepUpSessionId) {
    if (claims.authTime !== null && nowSeconds - claims.authTime > STEP_UP_MAX_AGE_SECONDS) {
      return fail("step_up_failed");
    }
    const sameSession =
      existing !== null &&
      existing.id === transaction.stepUpSessionId &&
      existing.subject === claims.subject &&
      existing.issuer === config[kind].issuer.href;
    if (sameSession) {
      carried = {
        familyId: existing.familyId,
        handle: existing.handle,
        csrfToken: existing.csrfToken,
        activeTenantId: existing.activeTenantId,
        // The school's idle timeout stays with the session (FR-IAM-003).
        idleTimeoutMs: existing.idleTimeoutMs,
      };
    } else {
      // Someone else signed in at the step-up prompt: start clean, go home.
      next = DEFAULT_NEXT[kind];
    }
  }
  if (existing) {
    // New session id after every sign-in or step-up (session fixation defence).
    if (!carried.familyId) await revokeAtIdp(runtime, existing);
    await store.revoke(existing.id);
  }

  const { cookieValue, session } = await store.create({
    kind,
    subject: claims.subject,
    issuer: config[kind].issuer.href,
    displayName: claims.displayName,
    authTime: claims.authTime,
    mfa: claims.mfa,
    tokens: {
      accessToken: tokens.accessToken,
      refreshToken: tokens.refreshToken,
      idToken: tokens.idToken,
    },
    accessExpiresAt: tokens.accessExpiresAt,
    ...carried,
    // The support session is pinned to the grant's school (the API refuses any other).
    ...(support ? { activeTenantId: support.tenantId } : {}),
  });
  if (support) {
    const started = await startSupportSession(
      runtime,
      session,
      tokens.accessToken,
      support.requestId,
      requestId,
    );
    if (started !== "ok") {
      await revokeAtIdp(runtime, session);
      await store.revoke(session.id);
      return fail(started);
    }
    await recordLoginEvent(runtime, session, tokens.accessToken, requestId);
  }
  if (kind === "staff" && !transaction.stepUpSessionId) {
    const outcome = await afterStaffSignIn(runtime, session, tokens.accessToken, requestId);
    const locale = localeOf(next);
    if (outcome.kind === "mfa_required") {
      // A privileged role without MFA gets no session at all (FR-IAM-002).
      await store.revoke(session.id);
      return fail("mfa_required");
    }
    if (outcome.kind === "choose") {
      next = `/${locale}/choose-school?next=${encodeURIComponent(next)}`;
    } else if (outcome.kind === "none") {
      next = `/${locale}/no-access`;
    }
  }
  logEvent(
    transaction.stepUpSessionId ? "step_up_completed" : "signin_completed",
    { kind },
    "info",
  );
  return seeOther(runtime, next, [
    serializeCookie(sessionCookieName(kind, secure), cookieValue, { secure }),
    clearTransaction,
  ]);
}

/** POST /bff/auth/logout?kind= (CSRF): revoke server-side, then end the IdP session. */
export async function handleLogout(request: Request, runtime: AuthRuntime) {
  const requestId = requestIdFrom(request.headers);
  const requested = kindParam(request);
  const secure = runtime.config.secureCookies;
  // The school console signs out whichever school session it runs as (staff or support).
  const current =
    requested === "staff"
      ? await readSchoolSession(request, runtime, { touch: false })
      : await readSession(request, runtime, requested, { touch: false });
  const kind = current?.session.kind ?? requested;
  const clear = clearCookie(sessionCookieName(kind, secure), secure);
  if (!current) {
    return jsonResponse({ redirect_to: signedOutUrl(kind) }, { cookies: [clear], requestId });
  }
  if (!csrfOk(request, current.session, runtime)) return csrfFailed(requestId);

  await revokeAtIdp(runtime, current.session);
  await runtime.store.revoke(current.session.id);
  logEvent("signed_out", { kind }, "info");
  let redirectTo = signedOutUrl(kind);
  try {
    // client_id + post_logout_redirect_uri only: the ID token never goes to the browser.
    const endSession = await runtime.oidc[kind].endSessionUrl(null);
    if (endSession) redirectTo = endSession.href;
  } catch {
    logEvent("end_session_unavailable", { kind });
  }
  return jsonResponse({ redirect_to: redirectTo }, { cookies: [clear], requestId });
}

/**
 * Non-secret session facts for the UI. Never tokens (SEC-004). `idle_timeout_ms` is only a
 * hint for the warning dialog: the store enforces the timeout on every request.
 */
export function sessionInfo(session: Session, now: number) {
  return {
    authenticated: true as const,
    kind: session.kind,
    display_name: session.displayName,
    active_tenant_id: session.activeTenantId,
    mfa: session.mfa,
    csrf_token: session.csrfToken,
    idle_timeout_ms: session.idleTimeoutMs,
    idle_expires_at: new Date(session.idleExpiresAt).toISOString(),
    absolute_expires_at: new Date(session.absoluteExpiresAt).toISOString(),
    expires_in_ms: Math.max(0, Math.min(session.idleExpiresAt, session.absoluteExpiresAt) - now),
  };
}

/**
 * GET /bff/auth/session?kind= : read without sliding the idle timeout (polling must not
 * keep a shared PC signed in). POST (CSRF) : "Stay signed in", slides it.
 */
export async function handleSessionInfo(request: Request, runtime: AuthRuntime) {
  const requestId = requestIdFrom(request.headers);
  const kind = kindParam(request);
  const touch = request.method === "POST";
  const current =
    kind === "staff"
      ? await readSchoolSession(request, runtime, { touch: false })
      : await readSession(request, runtime, kind, { touch: false });
  if (!current) return jsonResponse({ authenticated: false, kind }, { requestId });
  let session = current.session;
  if (touch) {
    if (!csrfOk(request, session, runtime)) return csrfFailed(requestId);
    session = (await runtime.store.get(session.id, { touch: true })) ?? session;
  }
  return jsonResponse(sessionInfo(session, runtime.now()), { requestId });
}

function unauthenticated(requestId: string) {
  return problem(requestId, 401, "unauthenticated", "Sign in to continue", {
    detail: "Your session has ended. Sign in again.",
  });
}

/** GET /bff/auth/sessions?kind= (list own) · DELETE ?kind=&id=<handle> (CSRF, revoke one). */
export async function handleSessions(request: Request, runtime: AuthRuntime) {
  const requestId = requestIdFrom(request.headers);
  const kind = kindParam(request);
  const current = await readSession(request, runtime, kind, {
    touch: request.method === "DELETE",
  });
  if (!current) return unauthenticated(requestId);
  const owner = current.session;

  if (request.method === "DELETE") {
    if (!csrfOk(request, owner, runtime)) return csrfFailed(requestId);
    const handle = new URL(request.url).searchParams.get("id") ?? "";
    const revoked = /^[A-Za-z0-9_-]{16,64}$/.test(handle)
      ? await runtime.store.revokeByHandle(owner, handle)
      : null;
    if (!revoked) return problem(requestId, 404, "not_found", "Session not found");
    const secure = runtime.config.secureCookies;
    const cookies =
      revoked.id === owner.id ? [clearCookie(sessionCookieName(kind, secure), secure)] : [];
    const response = jsonResponse(null, { status: 204, cookies, requestId });
    response.headers.delete("Content-Type");
    return response;
  }

  const sessions = await runtime.store.listFor(owner);
  return jsonResponse(
    {
      data: sessions.map((session) => ({
        id: session.handle,
        kind: session.kind,
        created_at: new Date(session.createdAt).toISOString(),
        last_seen_at: new Date(session.lastSeenAt).toISOString(),
        current: session.id === owner.id,
      })),
      next_cursor: null,
    },
    { requestId },
  );
}

async function readSmallJson(request: Request): Promise<unknown> {
  const text = await request.text();
  if (text.length > MAX_SMALL_BODY) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

/** POST /bff/auth/active-tenant (CSRF) {tenant_id}: school staff switch school. */
export async function handleActiveTenant(request: Request, runtime: AuthRuntime) {
  const requestId = requestIdFrom(request.headers);
  const current = await readSession(request, runtime, "staff", { touch: true });
  if (!current) return unauthenticated(requestId);
  const session = current.session;
  if (!csrfOk(request, session, runtime)) return csrfFailed(requestId);

  const body = await readSmallJson(request);
  const tenantId =
    body && typeof body === "object" && "tenant_id" in body
      ? (body as { tenant_id: unknown }).tenant_id
      : null;
  if (typeof tenantId !== "string" || !UUID.test(tenantId)) {
    return problem(requestId, 422, "validation_error", "Validation failed", {
      errors: [{ field: "tenant_id", code: "invalid_uuid", message_key: "errors.invalid_uuid" }],
    });
  }

  const chosen = tenantId.toLowerCase();
  // The chosen school's idle timeout from the API's answer (MeOut `settings`), if any.
  let idleTimeoutMinutes: number | null = null;
  // Ask the API (it re-checks membership on every request anyway; never reveals tenants).
  try {
    const fresh = await runtime.refresher.ensureFresh(session);
    const stored = await runtime.store.tokens(fresh.id);
    if (!stored) return unauthenticated(requestId);
    const response = await callApi(runtime, {
      session: { ...fresh, activeTenantId: tenantId.toLowerCase() },
      accessToken: stored.tokens.accessToken,
      method: "POST",
      path: "/api/v1/me/active-tenant",
      incomingHeaders: new Headers({ "content-type": "application/json" }),
      body: new TextEncoder().encode(JSON.stringify({ tenant_id: tenantId.toLowerCase() })),
      requestId,
      headersTimeoutMs: 10_000,
    });
    if (response.ok && (response.headers.get("content-type") ?? "").includes("json")) {
      const me: unknown = await response.json().catch(() => null);
      idleTimeoutMinutes = schoolIdleTimeoutFromMe(me, chosen);
    } else {
      await response.body?.cancel();
    }
    if ([401, 403].includes(response.status)) {
      return problem(requestId, 403, "tenant_not_available", "You can't open this school", {
        detail: "Ask the school's office admin to give you access.",
      });
    }
    if (response.status >= 500 && response.status !== 501) {
      return problem(requestId, 503, "service_unavailable", "Try again in a moment");
    }
  } catch (error) {
    if (error instanceof SessionEndedError) return unauthenticated(requestId);
    return problem(requestId, 503, "service_unavailable", "Try again in a moment");
  }

  if (session.loginEventPending) {
    // First school chosen after sign-in: now the API can audit the login in its log.
    const stored = await runtime.store.tokens(session.id);
    if (stored) {
      await recordLoginEvent(
        runtime,
        { ...session, activeTenantId: chosen },
        stored.tokens.accessToken,
        requestId,
      );
    }
  }
  await runtime.store.setActiveTenant(session, chosen, {
    loginEventPending: false,
    idleTimeoutMinutes,
  });
  const updated = (await runtime.store.get(session.id)) ?? session;
  return jsonResponse(sessionInfo(updated, runtime.now()), { requestId });
}
