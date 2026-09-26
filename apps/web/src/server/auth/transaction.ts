import "server-only";
import type { SessionKind } from "@/server/config";
import { deriveKey, SealError, seal, unseal } from "@/server/session/crypto";

/**
 * Sign-in transaction: state, nonce and PKCE verifier between /login and /callback,
 * kept in a short-lived httpOnly cookie sealed with AES-256-GCM (confidential and
 * tamper-evident; key derived from SESSION_SECRET for this purpose only).
 */

export const TRANSACTION_TTL_SECONDS = 600;

export interface AuthTransaction {
  v: 1;
  kind: SessionKind;
  state: string;
  nonce: string;
  codeVerifier: string;
  next: string;
  /** Step-up re-authentication of the session with this storage id. */
  stepUpSessionId: string | null;
  expiresAt: number;
}

export class TransactionCodec {
  private readonly key: Buffer;

  constructor(
    sessionSecret: Uint8Array,
    private readonly now: () => number = Date.now,
  ) {
    this.key = deriveKey(sessionSecret, "auth-transaction");
  }

  encode(transaction: Omit<AuthTransaction, "v" | "expiresAt">): string {
    const value: AuthTransaction = {
      v: 1,
      ...transaction,
      expiresAt: this.now() + TRANSACTION_TTL_SECONDS * 1000,
    };
    return seal(this.key, JSON.stringify(value), `auth-tx:${transaction.kind}`);
  }

  /** Returns null when missing, tampered with, for the other kind, or expired. */
  decode(cookieValue: string | undefined, kind: SessionKind): AuthTransaction | null {
    if (!cookieValue) return null;
    try {
      const value = JSON.parse(unseal(this.key, cookieValue, `auth-tx:${kind}`)) as AuthTransaction;
      if (value.v !== 1 || value.kind !== kind || value.expiresAt <= this.now()) return null;
      return value;
    } catch (error) {
      if (error instanceof SealError || error instanceof SyntaxError) return null;
      throw error;
    }
  }
}
