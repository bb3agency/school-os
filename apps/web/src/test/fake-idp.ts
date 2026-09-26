/**
 * In-process fake OIDC provider for BFF tests (no network, no mock-oauth2-server image).
 * Serves discovery and the token endpoint through openid-client's customFetch, signs ID
 * tokens with a real RS256 key, enforces PKCE S256 and client authentication, and rotates
 * refresh tokens. `authorize()` plays the user signing in at the authorization endpoint.
 */
import { createHash, randomBytes } from "node:crypto";
import { exportJWK, generateKeyPair, SignJWT, type CryptoKey } from "jose";
import type { CustomFetch } from "@/server/auth/oidc";

export interface FakeUser {
  sub: string;
  name?: string;
  mfa?: boolean;
  /** Seconds since epoch; defaults to now. */
  authTime?: number;
}

interface PendingCode {
  clientId: string;
  redirectUri: string;
  codeChallenge: string;
  nonce: string;
  user: FakeUser;
}

export interface FakeIdpOptions {
  issuer: string;
  clients: Record<string, string>;
  endSession?: boolean;
  accessTokenTtl?: number;
}

export async function createFakeIdp(options: FakeIdpOptions) {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const jwk = { ...(await exportJWK(publicKey)), kid: "test-key", alg: "RS256", use: "sig" };
  const issuer = options.issuer.replace(/\/$/, "");
  const codes = new Map<string, PendingCode>();
  const refreshTokens = new Map<string, { user: FakeUser; clientId: string }>();
  const tokenRequests: URLSearchParams[] = [];
  const ttl = options.accessTokenTtl ?? 600;

  const metadata = {
    issuer,
    authorization_endpoint: `${issuer}/authorize`,
    token_endpoint: `${issuer}/token`,
    jwks_uri: `${issuer}/jwks`,
    revocation_endpoint: `${issuer}/revoke`,
    ...(options.endSession === false ? {} : { end_session_endpoint: `${issuer}/endsession` }),
    response_types_supported: ["code"],
    subject_types_supported: ["public"],
    id_token_signing_alg_values_supported: ["RS256"],
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["client_secret_post"],
  };

  async function sign(claims: Record<string, unknown>, key: CryptoKey, audience: string) {
    const now = Math.floor(Date.now() / 1000);
    return new SignJWT(claims)
      .setProtectedHeader({ alg: "RS256", kid: "test-key", typ: "JWT" })
      .setIssuer(issuer)
      .setAudience(audience)
      .setIssuedAt(now)
      .setExpirationTime(now + ttl)
      .sign(key);
  }

  async function issueTokens(user: FakeUser, clientId: string, nonce: string | null) {
    const authTime = user.authTime ?? Math.floor(Date.now() / 1000);
    const accessToken = await sign(
      {
        sub: user.sub,
        client_id: clientId,
        token_use: "access",
        auth_time: authTime,
        "sos:mfa": user.mfa ? "true" : "false",
        jti: randomBytes(8).toString("hex"),
      },
      privateKey,
      clientId,
    );
    const idToken = await sign(
      {
        sub: user.sub,
        auth_time: authTime,
        ...(nonce ? { nonce } : {}),
        ...(user.name ? { name: user.name } : {}),
      },
      privateKey,
      clientId,
    );
    const refreshToken = `rt-${randomBytes(16).toString("hex")}`;
    refreshTokens.set(refreshToken, { user, clientId });
    return {
      access_token: accessToken,
      id_token: idToken,
      refresh_token: refreshToken,
      token_type: "Bearer",
      expires_in: ttl,
    };
  }

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });

  const fetch: CustomFetch = async (input, init) => {
    const url = new URL(input);
    if (!url.href.startsWith(issuer)) return new Response("not found", { status: 404 });
    const path = url.pathname.slice(new URL(issuer).pathname.length);
    if (path === "/.well-known/openid-configuration") return json(metadata);
    if (path === "/jwks") return json({ keys: [jwk] });
    if (path === "/revoke") return new Response(null, { status: 200 });
    if (path !== "/token" || init.method !== "POST") return new Response("nope", { status: 404 });

    const body = new URLSearchParams(String(init.body));
    tokenRequests.push(body);
    const clientId = body.get("client_id") ?? "";
    if (!(clientId in options.clients) || options.clients[clientId] !== body.get("client_secret")) {
      return json({ error: "invalid_client" }, 401);
    }
    if (body.get("grant_type") === "authorization_code") {
      const pending = codes.get(body.get("code") ?? "");
      codes.delete(body.get("code") ?? "");
      if (!pending || pending.clientId !== clientId || pending.redirectUri !== body.get("redirect_uri")) {
        return json({ error: "invalid_grant" }, 400);
      }
      const challenge = createHash("sha256")
        .update(body.get("code_verifier") ?? "")
        .digest("base64url");
      if (challenge !== pending.codeChallenge) return json({ error: "invalid_grant" }, 400);
      return json(await issueTokens(pending.user, clientId, pending.nonce));
    }
    if (body.get("grant_type") === "refresh_token") {
      const current = refreshTokens.get(body.get("refresh_token") ?? "");
      refreshTokens.delete(body.get("refresh_token") ?? "");
      if (!current || current.clientId !== clientId) return json({ error: "invalid_grant" }, 400);
      const tokens = await issueTokens(current.user, clientId, null);
      return json({ ...tokens, id_token: undefined });
    }
    return json({ error: "unsupported_grant_type" }, 400);
  };

  /**
   * The user signs in at `authorizationUrl`; returns the redirect back to the client.
   * `tamper` lets a test change what the IdP sends back.
   */
  function authorize(
    authorizationUrl: string | URL,
    user: FakeUser,
    tamper: { state?: string; nonce?: string } = {},
  ): URL {
    const url = new URL(authorizationUrl);
    const redirectUri = url.searchParams.get("redirect_uri") ?? "";
    const code = randomBytes(12).toString("hex");
    if (url.searchParams.get("code_challenge_method") !== "S256") throw new Error("PKCE S256 required");
    codes.set(code, {
      clientId: url.searchParams.get("client_id") ?? "",
      redirectUri,
      codeChallenge: url.searchParams.get("code_challenge") ?? "",
      nonce: tamper.nonce ?? url.searchParams.get("nonce") ?? "",
      user,
    });
    const back = new URL(redirectUri);
    back.searchParams.set("code", code);
    back.searchParams.set("state", tamper.state ?? url.searchParams.get("state") ?? "");
    return back;
  }

  return { fetch, authorize, tokenRequests, refreshTokens, issuer };
}

export type FakeIdp = Awaited<ReturnType<typeof createFakeIdp>>;
