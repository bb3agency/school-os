// @vitest-environment node
import { randomBytes } from "node:crypto";
import { describe, expect, it } from "vitest";
import { constantTimeEqual, deriveKey, randomToken, seal, SealError, unseal } from "./crypto";

const secret = randomBytes(32);

describe("session crypto (SEC-006)", () => {
  it("round-trips tokens with AES-256-GCM and a fresh IV each time", () => {
    const key = deriveKey(secret, "session-tokens");
    const first = seal(key, "payload-1", "tokens:abc");
    const second = seal(key, "payload-1", "tokens:abc");
    expect(first).not.toBe(second);
    expect(first).not.toContain("payload-1");
    expect(unseal(key, first, "tokens:abc")).toBe("payload-1");
  });

  it("detects tampering with the ciphertext", () => {
    const key = deriveKey(secret, "session-tokens");
    const sealed = seal(key, "payload", "aad");
    const raw = Buffer.from(sealed.slice(3), "base64url");
    raw[raw.length - 20] = (raw[raw.length - 20] ?? 0) ^ 0x01;
    expect(() => unseal(key, `v1.${raw.toString("base64url")}`, "aad")).toThrow(SealError);
    expect(() => unseal(key, "v1.AAAA", "aad")).toThrow(SealError);
    expect(() => unseal(key, "v2.AAAA", "aad")).toThrow(SealError);
  });

  it("binds a ciphertext to its record (AAD) and to its key purpose", () => {
    const key = deriveKey(secret, "session-tokens");
    const sealed = seal(key, "payload", "tokens:session-a");
    expect(() => unseal(key, sealed, "tokens:session-b")).toThrow(SealError);
    expect(() => unseal(deriveKey(secret, "auth-transaction"), sealed, "tokens:session-a")).toThrow(
      SealError,
    );
    expect(() => unseal(deriveKey(randomBytes(32), "session-tokens"), sealed, "tokens:session-a")).toThrow(
      SealError,
    );
  });

  it("makes 256-bit session ids and compares secrets safely", () => {
    const id = randomToken(32);
    expect(id).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(constantTimeEqual(id, id)).toBe(true);
    expect(constantTimeEqual(id, `${id}x`)).toBe(false);
    expect(constantTimeEqual(id, "")).toBe(false);
  });
});
