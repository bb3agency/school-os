import "server-only";
import { decodeJwt } from "jose";
import * as client from "openid-client";
import type { OidcClientConfig, SessionKind } from "@/server/config";
import type { TokenSet } from "@/server/session/store";

/**
 * OIDC relying party for the BFF (openid-client v6): Authorization Code + PKCE (S256),
 * refresh, end session (FR-IAM-001, FR-IAM-004, SEC-005; ADR-0012, ADR-0018).
 *
 * Staff and operators use separate clients (and, in the reference setup, separate Cognito
 * user pools). Discovery results are cached per client and retried after a failure.
 */

export type CustomFetch = client.CustomFetch;

export interface OidcTokens extends TokenSet {
  accessExpiresAt: number;
  /** ID token claims (validated by openid-client: iss, aud, exp, iat, nonce). */
  idClaims: Record<string, unknown> | null;
}

export interface AuthorizationRequest {
  state: string;
  nonce: string;
  codeChallenge: string;
  /** Step-up: force re-authentication (prompt=login, max_age=0; ADR-0018). */
  stepUp: boolean;
}

export interface OidcClient {
  readonly kind: SessionKind;
  authorizationUrl(request: AuthorizationRequest): Promise<URL>;
  exchangeCode(
    callbackUrl: URL,
    checks: { state: string; nonce: string; codeVerifier: string },
  ): Promise<OidcTokens>;
  refresh(refreshToken: string): Promise<OidcTokens>;
  /** RP-initiated logout URL when the IdP advertises end_session_endpoint, else null. */
  endSessionUrl(idToken: string | null): Promise<URL | null>;
  /** Best effort (RFC 7009) when the IdP has a revocation endpoint. */
  revoke(refreshToken: string): Promise<void>;
}

/** The IdP refused a grant (expired/revoked refresh token, bad code): sign in again. */
export class OidcGrantError extends Error {
  constructor(readonly code: string) {
    super(`OIDC grant refused: ${code}`);
    this.name = "OidcGrantError";
  }
}

const DEFAULT_ACCESS_TTL_MS = 5 * 60_000;

function toGrantError(error: unknown): unknown {
  if (error instanceof client.ResponseBodyError) return new OidcGrantError(error.error);
  if (error instanceof client.AuthorizationResponseError) return new OidcGrantError(error.error);
  return error;
}

export function createOidcClient(
  config: OidcClientConfig,
  options: { fetch?: CustomFetch; now?: () => number; timeoutSeconds?: number } = {},
): OidcClient {
  const now = options.now ?? Date.now;
  let discovered: Promise<client.Configuration> | null = null;

  function configuration(): Promise<client.Configuration> {
    if (!discovered) {
      const execute = config.allowInsecureIssuer ? [client.allowInsecureRequests] : [];
      discovered = client
        .discovery(
          config.issuer,
          config.clientId,
          config.clientSecret,
          client.ClientSecretPost(config.clientSecret),
          {
            execute,
            timeout: options.timeoutSeconds ?? 10,
            ...(options.fetch ? { [client.customFetch]: options.fetch } : {}),
          },
        )
        .catch((error: unknown) => {
          discovered = null;
          throw error;
        });
    }
    return discovered;
  }

  function toTokens(response: client.TokenEndpointResponse & client.TokenEndpointResponseHelpers) {
    const expiresIn = response.expiresIn();
    let accessExpiresAt = now() + DEFAULT_ACCESS_TTL_MS;
    if (expiresIn !== undefined) {
      accessExpiresAt = now() + expiresIn * 1000;
    } else {
      try {
        const exp = decodeJwt(response.access_token).exp;
        if (typeof exp === "number") accessExpiresAt = exp * 1000;
      } catch {
        // Opaque access token without expires_in: fall back to the default.
      }
    }
    const claims = response.claims();
    const tokens: OidcTokens = {
      accessToken: response.access_token,
      refreshToken: response.refresh_token ?? null,
      idToken: response.id_token ?? null,
      accessExpiresAt,
      idClaims: claims ? { ...claims } : null,
    };
    return tokens;
  }

  return {
    kind: config.kind,

    async authorizationUrl(request) {
      const parameters: Record<string, string> = {
        redirect_uri: config.redirectUri,
        scope: config.scope,
        response_type: "code",
        state: request.state,
        nonce: request.nonce,
        code_challenge: request.codeChallenge,
        code_challenge_method: "S256",
      };
      if (request.stepUp) {
        parameters.prompt = "login";
        parameters.max_age = "0";
      }
      return client.buildAuthorizationUrl(await configuration(), parameters);
    },

    async exchangeCode(callbackUrl, checks) {
      try {
        const response = await client.authorizationCodeGrant(await configuration(), callbackUrl, {
          pkceCodeVerifier: checks.codeVerifier,
          expectedState: checks.state,
          expectedNonce: checks.nonce,
          idTokenExpected: true,
        });
        return toTokens(response);
      } catch (error) {
        throw toGrantError(error);
      }
    },

    async refresh(refreshToken) {
      try {
        return toTokens(await client.refreshTokenGrant(await configuration(), refreshToken));
      } catch (error) {
        throw toGrantError(error);
      }
    },

    async endSessionUrl(idToken) {
      const configured = await configuration();
      if (!configured.serverMetadata().end_session_endpoint) return null;
      const parameters: Record<string, string> = {
        post_logout_redirect_uri: config.postLogoutRedirectUri,
      };
      if (idToken) parameters.id_token_hint = idToken;
      return client.buildEndSessionUrl(configured, parameters);
    },

    async revoke(refreshToken) {
      const configured = await configuration();
      if (!configured.serverMetadata().revocation_endpoint) return;
      await client.tokenRevocation(configured, refreshToken, {
        token_type_hint: "refresh_token",
      });
    },
  };
}

export interface SessionClaims {
  subject: string;
  displayName: string | null;
  authTime: number | null;
  mfa: boolean;
}

function claimsOf(token: string | null): Record<string, unknown> {
  if (!token) return {};
  try {
    return decodeJwt(token) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function hasMfa(claims: Record<string, unknown>): boolean {
  const flag = claims["sos:mfa"];
  if (flag === "true" || flag === true) return true;
  const amr = claims.amr;
  return Array.isArray(amr) && amr.includes("mfa");
}

/**
 * What the session keeps from the tokens. The subject comes from the validated ID token.
 * The MFA flag and auth_time are for the UI only; the API re-checks them from the access
 * token on every request (ADR-0018).
 */
export function sessionClaims(tokens: OidcTokens): SessionClaims | null {
  const id = tokens.idClaims ?? {};
  const access = claimsOf(tokens.accessToken);
  const subject = typeof id.sub === "string" ? id.sub : null;
  if (!subject) return null;
  const name = [id.name, id.preferred_username, access.username].find(
    (value): value is string => typeof value === "string" && value.trim() !== "",
  );
  const authTime = [access.auth_time, id.auth_time].find(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  return {
    subject,
    displayName: name ? name.trim().slice(0, 120) : null,
    authTime: authTime ?? null,
    mfa: hasMfa(access) || hasMfa(id),
  };
}
