import "server-only";

/**
 * BFF configuration from the environment (build contract §6; SEC-004, SEC-006).
 *
 * Validated once at server start (src/instrumentation.ts) and again lazily by the auth
 * runtime. Error messages name the variable, never its value.
 */

/**
 * `support`: a SchoolOS operator signed in to the SCHOOL app during break-glass, through the
 * dedicated support app client of the operator pool (ADR-0023 option C). Own cookie, own
 * session; the school console accepts it when there is no staff session.
 */
export type SessionKind = "staff" | "operator" | "support";

export interface OidcClientConfig {
  kind: SessionKind;
  issuer: URL;
  clientId: string;
  clientSecret: string;
  /** Absolute callback URL registered at the IdP. */
  redirectUri: string;
  /** Absolute URL the IdP sends the browser to after end-session. */
  postLogoutRedirectUri: string;
  scope: string;
  /** Plain-http issuer (only the local dev stub on a loopback host). */
  allowInsecureIssuer: boolean;
}

export interface AuthConfig {
  appBaseUrl: URL;
  /** false only for plain-http loopback runs (local dev, e2e smoke): no Secure, no __Host-. */
  secureCookies: boolean;
  sessionSecret: Uint8Array;
  redisUrl: string;
  apiInternalUrl: URL;
  serviceTokenKey: Uint8Array;
  staff: OidcClientConfig;
  operator: OidcClientConfig;
  /** Break-glass support client of the operator pool (ADR-0023); see `supportEnabled`. */
  support: OidcClientConfig;
  /** SOS_DEPLOYMENT_MODE=dedicated switches the control plane off on this host (contract §1). */
  platformEnabled: boolean;
  /**
   * SUPPORT_OIDC_CLIENT_ID set: operators can use an approved break-glass grant in this school
   * app. Unset: the support routes answer 404 and approved access cannot be used (fail closed).
   */
  supportEnabled: boolean;
}

/** Where people land after signing out (outside the authenticated route groups). */
export function signedOutPath(kind: SessionKind): string {
  if (kind === "operator") return "/signed-out?kind=operator";
  if (kind === "support") return "/signed-out?kind=support";
  return "/signed-out";
}

export class ConfigError extends Error {
  constructor(readonly problems: readonly string[]) {
    super(`Invalid web BFF configuration: ${problems.join("; ")}`);
    this.name = "ConfigError";
  }
}

export const MIN_SECRET_BYTES = 32;
const OIDC_SCOPE = "openid profile email";
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const DEV_PLACEHOLDER = /^(dev-only|change-me)/i;

type Env = Readonly<Record<string, string | undefined>>;

export function isLoopbackHost(hostname: string): boolean {
  return LOOPBACK_HOSTS.has(hostname.toLowerCase());
}

/**
 * Hosts a local dev OIDC stub may use: loopback, or a `*.localhost` name (RFC 6761), which
 * browsers resolve to loopback and the compose network resolves to the stub container.
 */
export function isLocalDevHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  return isLoopbackHost(host) || (host.endsWith(".localhost") && host.length > ".localhost".length);
}

function parseUrl(value: string | undefined): URL | null {
  if (!value) return null;
  try {
    return new URL(value.trim());
  } catch {
    return null;
  }
}

/**
 * Parse and validate the web BFF environment. Throws ConfigError listing every problem.
 *
 * Plain http is accepted only when APP_BASE_URL points at a loopback host (local runs);
 * any other deployment must be https, which turns on `Secure` + `__Host-` cookies.
 */
export function loadAuthConfig(env: Env = process.env): AuthConfig {
  const problems: string[] = [];
  const need = (name: string): string => {
    const value = env[name]?.trim();
    if (!value) problems.push(`${name} is required`);
    return value ?? "";
  };

  const appBaseUrl = parseUrl(env.APP_BASE_URL);
  let secureCookies = true;
  if (!appBaseUrl) {
    problems.push("APP_BASE_URL must be an absolute URL");
  } else if (appBaseUrl.protocol === "http:") {
    if (!isLoopbackHost(appBaseUrl.hostname)) {
      problems.push("APP_BASE_URL must use https (plain http is allowed only for localhost)");
    }
    secureCookies = false;
  } else if (appBaseUrl.protocol !== "https:") {
    problems.push("APP_BASE_URL must use https");
  }

  const secret = (name: string): Uint8Array => {
    const value = need(name);
    const bytes = new TextEncoder().encode(value);
    if (value && bytes.length < MIN_SECRET_BYTES) {
      problems.push(`${name} must be at least ${MIN_SECRET_BYTES} bytes`);
    }
    if (value && secureCookies && DEV_PLACEHOLDER.test(value)) {
      problems.push(`${name} still has a dev-only placeholder value`);
    }
    return bytes;
  };
  const sessionSecret = secret("SESSION_SECRET");
  const serviceTokenKey = secret("SOS_SERVICE_TOKEN_KEY");

  const redisUrl = need("REDIS_URL");
  if (redisUrl && !/^rediss?:\/\//.test(redisUrl)) {
    problems.push("REDIS_URL must start with redis:// or rediss://");
  }

  const apiInternalUrl = parseUrl(env.API_INTERNAL_URL);
  if (!apiInternalUrl || !/^https?:$/.test(apiInternalUrl.protocol)) {
    problems.push("API_INTERNAL_URL must be an absolute http(s) URL");
  }

  const base = appBaseUrl?.origin ?? "https://invalid.example";
  const client = (
    kind: SessionKind,
    issuerVar: string,
    idVar: string,
    secretVar: string,
    callbackPath: string,
    issuerValue: string | undefined = env[issuerVar],
  ): OidcClientConfig => {
    const issuer = parseUrl(issuerValue);
    let allowInsecureIssuer = false;
    if (!issuer) {
      problems.push(`${issuerVar} must be an absolute URL`);
    } else if (issuer.protocol === "http:") {
      // The dev stub only: http issuer on loopback/*.localhost, and only when the app is local.
      if (secureCookies || !isLocalDevHost(issuer.hostname)) {
        problems.push(`${issuerVar} must use https`);
      }
      allowInsecureIssuer = true;
    } else if (issuer.protocol !== "https:") {
      problems.push(`${issuerVar} must use https`);
    }
    return {
      kind,
      issuer: issuer ?? new URL("https://invalid.example"),
      clientId: need(idVar),
      clientSecret: need(secretVar),
      redirectUri: `${base}${callbackPath}`,
      postLogoutRedirectUri: `${base}${signedOutPath(kind)}`,
      scope: OIDC_SCOPE,
      allowInsecureIssuer,
    };
  };

  const staff = client(
    "staff",
    "OIDC_ISSUER",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "/bff/auth/callback",
  );
  const operator = client(
    "operator",
    "PLATFORM_OIDC_ISSUER",
    "PLATFORM_OIDC_CLIENT_ID",
    "PLATFORM_OIDC_CLIENT_SECRET",
    "/bff/auth/platform/callback",
  );

  // Break-glass support sign-in (ADR-0023): a third app client, in the OPERATOR pool.
  const supportEnabled = Boolean(env.SUPPORT_OIDC_CLIENT_ID?.trim());
  const supportIssuer = env.SUPPORT_OIDC_ISSUER?.trim() || env.PLATFORM_OIDC_ISSUER;
  let support: OidcClientConfig;
  if (supportEnabled) {
    support = client(
      "support",
      "SUPPORT_OIDC_ISSUER",
      "SUPPORT_OIDC_CLIENT_ID",
      "SUPPORT_OIDC_CLIENT_SECRET",
      "/bff/auth/support/callback",
      supportIssuer,
    );
    if (support.clientId && [staff.clientId, operator.clientId].includes(support.clientId)) {
      problems.push("SUPPORT_OIDC_CLIENT_ID must be its own app client");
    }
    if (support.issuer.href === staff.issuer.href) {
      problems.push("SUPPORT_OIDC_ISSUER must be the operator pool, not the staff pool");
    }
  } else {
    // Placeholder (never used: every support route answers 404 first).
    support = { ...operator, kind: "support", clientId: "", clientSecret: "" };
  }

  if (problems.length > 0) throw new ConfigError(problems);

  return {
    appBaseUrl: appBaseUrl as URL,
    secureCookies,
    sessionSecret,
    redisUrl,
    apiInternalUrl: apiInternalUrl as URL,
    serviceTokenKey,
    staff,
    operator,
    support,
    platformEnabled: (env.SOS_DEPLOYMENT_MODE ?? "shared").trim() !== "dedicated",
    supportEnabled,
  };
}
