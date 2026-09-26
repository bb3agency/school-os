import "server-only";
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  hkdfSync,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";

/**
 * Session cryptography (SEC-004, SEC-006, docs/07 §5.2).
 *
 * - Tokens are stored in Valkey sealed with AES-256-GCM. The additional authenticated data
 *   binds each ciphertext to its purpose and record, so a sealed value cannot be moved to
 *   another session.
 * - Keys are derived with HKDF-SHA256 from SESSION_SECRET, one key per purpose.
 * - Session ids are 256-bit random values; Valkey keys use their SHA-256 so a dump of the
 *   store does not contain usable cookies.
 */

const SEAL_VERSION = "v1";
const IV_BYTES = 12;
const TAG_BYTES = 16;
const HKDF_SALT = "schoolos-web-bff-v1";

export type KeyPurpose = "session-tokens" | "auth-transaction";

export class SealError extends Error {
  constructor() {
    super("sealed value is invalid or was tampered with");
    this.name = "SealError";
  }
}

export function deriveKey(secret: Uint8Array, purpose: KeyPurpose): Buffer {
  return Buffer.from(hkdfSync("sha256", secret, HKDF_SALT, purpose, 32));
}

/** AES-256-GCM: `v1.<base64url(iv | ciphertext | tag)>`. */
export function seal(key: Buffer, plaintext: string, aad: string): string {
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  cipher.setAAD(Buffer.from(aad, "utf8"));
  const body = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return `${SEAL_VERSION}.${Buffer.concat([iv, body, cipher.getAuthTag()]).toString("base64url")}`;
}

export function unseal(key: Buffer, sealed: string, aad: string): string {
  const [version, payload, extra] = sealed.split(".");
  if (version !== SEAL_VERSION || !payload || extra !== undefined) throw new SealError();
  const raw = Buffer.from(payload, "base64url");
  if (raw.length < IV_BYTES + TAG_BYTES) throw new SealError();
  const iv = raw.subarray(0, IV_BYTES);
  const tag = raw.subarray(raw.length - TAG_BYTES);
  const body = raw.subarray(IV_BYTES, raw.length - TAG_BYTES);
  try {
    const decipher = createDecipheriv("aes-256-gcm", key, iv);
    decipher.setAAD(Buffer.from(aad, "utf8"));
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(body), decipher.final()]).toString("utf8");
  } catch {
    throw new SealError();
  }
}

/** 256-bit random value, base64url (43 characters). */
export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

export function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("base64url");
}

/** Constant-time string comparison (hashes first so lengths never leak). */
export function constantTimeEqual(a: string, b: string): boolean {
  const left = createHash("sha256").update(a, "utf8").digest();
  const right = createHash("sha256").update(b, "utf8").digest();
  return timingSafeEqual(left, right) && a.length === b.length;
}
