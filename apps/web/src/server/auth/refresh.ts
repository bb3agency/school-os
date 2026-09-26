import "server-only";
import type { SessionKind } from "@/server/config";
import { logEvent } from "@/server/log";
import { randomToken } from "@/server/session/crypto";
import {
  sessionKeys,
  type Session,
  type SessionStore,
  type TokenSet,
} from "@/server/session/store";
import { OidcGrantError, type OidcClient } from "./oidc";

/**
 * Access-token refresh with rotation and reuse detection (FR-IAM-004, docs/07 §5.2).
 *
 * - Refresh when the access token expires within 60 s (or the API said `token_expired`).
 * - One refresh per session at a time across all web tasks: Valkey lock `SET NX PX`.
 *   Waiters poll until the holder has stored new tokens, then use them. Within one process
 *   concurrent callers share the same promise.
 * - Every refresh token is spent at most once: its hash goes into the family's used set.
 *   Presenting a spent token again means a stale or stolen copy is in use, so the whole
 *   family (every session descended from that sign-in) is revoked.
 */

export const REFRESH_SKEW_MS = 60_000;

export type SessionEndReason =
  "session_gone" | "no_refresh_token" | "refresh_failed" | "refresh_reuse" | "subject_changed";

export class SessionEndedError extends Error {
  constructor(readonly reason: SessionEndReason) {
    super(`session ended: ${reason}`);
    this.name = "SessionEndedError";
  }
}

/** Another request held the refresh lock for too long; the caller may retry. */
export class RefreshBusyError extends Error {
  constructor() {
    super("token refresh in progress");
    this.name = "RefreshBusyError";
  }
}

export interface RefresherOptions {
  store: SessionStore;
  oidc: Record<SessionKind, OidcClient>;
  now?: () => number;
  lockTtlMs?: number;
  lockWaitMs?: number;
  pollMs?: number;
}

interface FreshnessCheck {
  force: boolean;
  /** With `force`: the access token the API just refused. */
  staleAccessToken?: string | undefined;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export class TokenRefresher {
  private readonly inflight = new Map<string, Promise<Session>>();
  private readonly now: () => number;

  constructor(private readonly options: RefresherOptions) {
    this.now = options.now ?? Date.now;
  }

  /** Session whose access token is valid for at least 60 s (refreshing if needed). */
  async ensureFresh(session: Session, check: FreshnessCheck = { force: false }): Promise<Session> {
    if (!check.force && session.accessExpiresAt - this.now() > REFRESH_SKEW_MS) return session;
    const running = this.inflight.get(session.id);
    if (running) return running;
    const promise = this.refreshUnderLock(session, check).finally(() => {
      this.inflight.delete(session.id);
    });
    this.inflight.set(session.id, promise);
    return promise;
  }

  private async isFresh(session: Session, check: FreshnessCheck): Promise<boolean> {
    if (session.accessExpiresAt - this.now() <= REFRESH_SKEW_MS) return false;
    if (!check.force) return true;
    const stored = await this.options.store.tokens(session.id);
    return stored !== null && stored.tokens.accessToken !== check.staleAccessToken;
  }

  private async refreshUnderLock(session: Session, check: FreshnessCheck): Promise<Session> {
    const { store } = this.options;
    const lockKey = sessionKeys.lock(session.id);
    const lockToken = randomToken(16);
    const deadline = this.now() + (this.options.lockWaitMs ?? 8_000);
    const lockTtl = this.options.lockTtlMs ?? 15_000;

    while (!(await store.kv.set(lockKey, lockToken, { nx: true, pxMs: lockTtl }))) {
      if (this.now() >= deadline) throw new RefreshBusyError();
      await sleep(this.options.pollMs ?? 50);
      const current = await store.get(session.id);
      if (!current) throw new SessionEndedError("session_gone");
      if (await this.isFresh(current, check)) return current;
    }

    try {
      const current = await store.get(session.id);
      if (!current) throw new SessionEndedError("session_gone");
      if (await this.isFresh(current, check)) return current;
      return await this.refresh(current);
    } finally {
      await store.kv.delIfEquals(lockKey, lockToken);
    }
  }

  private async refresh(session: Session): Promise<Session> {
    const { store } = this.options;
    const stored = await store.tokens(session.id);
    const refreshToken = stored?.tokens.refreshToken;
    if (!stored || !refreshToken) {
      await store.revoke(session.id);
      throw new SessionEndedError("no_refresh_token");
    }

    if (!(await store.spendRefreshToken(session.familyId, refreshToken))) {
      await store.revokeFamily(session.familyId);
      logEvent("refresh_token_reuse_detected", { kind: session.kind }, "error");
      throw new SessionEndedError("refresh_reuse");
    }

    const client = this.options.oidc[session.kind];
    let fresh;
    try {
      fresh = await client.refresh(refreshToken);
    } catch (error) {
      if (error instanceof OidcGrantError) {
        await store.revoke(session.id);
        logEvent("refresh_refused", { kind: session.kind, code: error.code });
        throw new SessionEndedError("refresh_failed");
      }
      // Network or IdP outage: the token may still be valid, allow a later retry.
      await store.unspendRefreshToken(session.familyId, refreshToken);
      throw error;
    }

    const newSubject = fresh.idClaims?.sub;
    if (typeof newSubject === "string" && newSubject !== session.subject) {
      await store.revokeFamily(session.familyId);
      throw new SessionEndedError("subject_changed");
    }

    const rotated = fresh.refreshToken !== null && fresh.refreshToken !== refreshToken;
    if (!rotated) await store.unspendRefreshToken(session.familyId, refreshToken);
    const tokens: TokenSet = {
      accessToken: fresh.accessToken,
      refreshToken: rotated ? fresh.refreshToken : refreshToken,
      idToken: fresh.idToken ?? stored.tokens.idToken,
    };
    await store.saveTokens(session, tokens, fresh.accessExpiresAt);
    return { ...session, accessExpiresAt: fresh.accessExpiresAt };
  }
}
