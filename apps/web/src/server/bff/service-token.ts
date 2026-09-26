import "server-only";
import { randomBytes } from "node:crypto";
import { SignJWT } from "jose";

/**
 * BFF → API service token (build contract §1, docs/09 §1; SEC-004; trust boundary TB2).
 * Must match apps/api/app/identity/service_token.py exactly:
 * HS256, iss "sos-web", aud "sos-api" (single string), iat, exp = iat + 60 s,
 * jti 16–128 chars of [A-Za-z0-9_-], sent in X-Service-Token. One token per request
 * (the API refuses a reused jti).
 */
export const SERVICE_TOKEN_HEADER = "X-Service-Token";
export const SERVICE_TOKEN_ISSUER = "sos-web";
export const SERVICE_TOKEN_AUDIENCE = "sos-api";
export const SERVICE_TOKEN_TTL_SECONDS = 60;

export async function mintServiceToken(
  key: Uint8Array,
  now: () => number = Date.now,
): Promise<string> {
  const issuedAt = Math.floor(now() / 1000);
  return new SignJWT({})
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuer(SERVICE_TOKEN_ISSUER)
    .setAudience(SERVICE_TOKEN_AUDIENCE)
    .setIssuedAt(issuedAt)
    .setExpirationTime(issuedAt + SERVICE_TOKEN_TTL_SECONDS)
    .setJti(randomBytes(24).toString("base64url"))
    .sign(key);
}
