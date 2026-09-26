import "server-only";
import type { SessionKind } from "@/server/config";
import { deriveKey, randomToken, seal, sha256, unseal } from "./crypto";
import type { KeyValue } from "./kv";

/**
 * Server-side BFF sessions in Valkey (docs/07 §5.2; SEC-004, SEC-006, FR-IAM-003).
 *
 * The browser holds only an opaque 256-bit session id. Valkey keys use SHA-256 of that id.
 * Each session is three keys so that concurrent requests never overwrite each other:
 *   sos:web:sess:<id>        metadata (JSON; no tokens)
 *   sos:web:sess:<id>:tok    AES-256-GCM sealed tokens + access expiry (written under the
 *                            refresh lock only)
 *   sos:web:sess:<id>:seen   last-seen time (slid on every authenticated request)
 * plus per-subject and per-family index sets and the family's used-refresh-token set.
 */

export const SESSION_KEY_PREFIX = "sos:web:sess:";
/** Idle timeout (docs/07 §5.2). Tenant-configurable 5–30 min later; default for now. */
export const IDLE_TIMEOUT_MS = 15 * 60_000;
/** Absolute lifetime: 12 h for school staff, 8 h for operators (docs/16 §2). */
export const ABSOLUTE_TIMEOUT_MS: Record<SessionKind, number> = {
  staff: 12 * 60 * 60_000,
  operator: 8 * 60 * 60_000,
};

const SESSION_ID = /^[A-Za-z0-9_-]{43}$/;

export interface TokenSet {
  accessToken: string;
  refreshToken: string | null;
  idToken: string | null;
}

export interface Session {
  /** Storage id: SHA-256 of the cookie value. Never the cookie value itself. */
  id: string;
  kind: SessionKind;
  subject: string;
  issuer: string;
  /** Public id shown in "your sessions" lists (not usable as a cookie). */
  handle: string;
  familyId: string;
  activeTenantId: string | null;
  displayName: string | null;
  createdAt: number;
  lastSeenAt: number;
  /** Seconds since epoch, from the IdP (step-up freshness). */
  authTime: number | null;
  mfa: boolean;
  csrfToken: string;
  accessExpiresAt: number;
  idleExpiresAt: number;
  absoluteExpiresAt: number;
}

interface StoredMeta {
  v: 1;
  kind: SessionKind;
  sub: string;
  iss: string;
  handle: string;
  family: string;
  tenant: string | null;
  name: string | null;
  created: number;
  authTime: number | null;
  mfa: boolean;
  csrf: string;
}

interface StoredTokens {
  sealed: string;
  accessExp: number;
}

export interface NewSession {
  kind: SessionKind;
  subject: string;
  issuer: string;
  displayName: string | null;
  authTime: number | null;
  mfa: boolean;
  tokens: TokenSet;
  accessExpiresAt: number;
  activeTenantId?: string | null;
  /** Keep the refresh-token family (step-up re-authentication of the same session). */
  familyId?: string;
  handle?: string;
  csrfToken?: string;
}

export interface SessionStoreOptions {
  now?: () => number;
  idleTimeoutMs?: number;
}

const key = {
  meta: (id: string) => `${SESSION_KEY_PREFIX}${id}`,
  tokens: (id: string) => `${SESSION_KEY_PREFIX}${id}:tok`,
  seen: (id: string) => `${SESSION_KEY_PREFIX}${id}:seen`,
  index: (kind: SessionKind, issuer: string, subject: string) =>
    `${SESSION_KEY_PREFIX}idx:${kind}:${sha256(`${issuer}\n${subject}`)}`,
  family: (family: string) => `${SESSION_KEY_PREFIX}fam:${family}`,
  usedRefresh: (family: string) => `${SESSION_KEY_PREFIX}rt:${family}`,
  lock: (id: string) => `${SESSION_KEY_PREFIX}lock:${id}`,
};

export const sessionKeys = key;

export class SessionStore {
  private readonly tokenKey: Buffer;
  private readonly now: () => number;
  readonly idleTimeoutMs: number;

  constructor(
    readonly kv: KeyValue,
    sessionSecret: Uint8Array,
    options: SessionStoreOptions = {},
  ) {
    this.tokenKey = deriveKey(sessionSecret, "session-tokens");
    this.now = options.now ?? Date.now;
    this.idleTimeoutMs = options.idleTimeoutMs ?? IDLE_TIMEOUT_MS;
  }

  static isWellFormedId(cookieValue: string | undefined | null): cookieValue is string {
    return typeof cookieValue === "string" && SESSION_ID.test(cookieValue);
  }

  storageId(cookieValue: string): string {
    return sha256(cookieValue);
  }

  private ttl(meta: StoredMeta, now: number): number {
    const absoluteLeft = meta.created + ABSOLUTE_TIMEOUT_MS[meta.kind] - now;
    return Math.max(1, Math.min(this.idleTimeoutMs, absoluteLeft));
  }

  private toSession(id: string, meta: StoredMeta, seen: number, accessExp: number): Session {
    return {
      id,
      kind: meta.kind,
      subject: meta.sub,
      issuer: meta.iss,
      handle: meta.handle,
      familyId: meta.family,
      activeTenantId: meta.tenant,
      displayName: meta.name,
      createdAt: meta.created,
      lastSeenAt: seen,
      authTime: meta.authTime,
      mfa: meta.mfa,
      csrfToken: meta.csrf,
      accessExpiresAt: accessExp,
      idleExpiresAt: seen + this.idleTimeoutMs,
      absoluteExpiresAt: meta.created + ABSOLUTE_TIMEOUT_MS[meta.kind],
    };
  }

  private sealTokens(id: string, tokens: TokenSet): string {
    return seal(this.tokenKey, JSON.stringify(tokens), `tokens:${id}`);
  }

  async create(input: NewSession): Promise<{ cookieValue: string; session: Session }> {
    const now = this.now();
    const cookieValue = randomToken(32);
    const id = this.storageId(cookieValue);
    const meta: StoredMeta = {
      v: 1,
      kind: input.kind,
      sub: input.subject,
      iss: input.issuer,
      handle: input.handle ?? randomToken(16),
      family: input.familyId ?? randomToken(16),
      tenant: input.activeTenantId ?? null,
      name: input.displayName,
      created: now,
      authTime: input.authTime,
      mfa: input.mfa,
      csrf: input.csrfToken ?? randomToken(32),
    };
    const pxMs = this.ttl(meta, now);
    const stored: StoredTokens = {
      sealed: this.sealTokens(id, input.tokens),
      accessExp: input.accessExpiresAt,
    };
    await this.kv.set(key.tokens(id), JSON.stringify(stored), { pxMs });
    await this.kv.set(key.seen(id), String(now), { pxMs });
    await this.kv.set(key.meta(id), JSON.stringify(meta), { pxMs });
    const absolute = ABSOLUTE_TIMEOUT_MS[meta.kind];
    const indexKey = key.index(meta.kind, meta.iss, meta.sub);
    await this.kv.sadd(indexKey, id);
    await this.kv.pexpire(indexKey, absolute);
    await this.kv.sadd(key.family(meta.family), id);
    await this.kv.pexpire(key.family(meta.family), absolute);
    return { cookieValue, session: this.toSession(id, meta, now, input.accessExpiresAt) };
  }

  /** Look up by cookie value. Expired sessions are revoked and return null. */
  async load(cookieValue: string | undefined | null, options: { touch: boolean }) {
    if (!SessionStore.isWellFormedId(cookieValue)) return null;
    return this.get(this.storageId(cookieValue), options);
  }

  /** Look up by storage id; `touch` slides the idle timeout (an authenticated request). */
  async get(id: string, options: { touch: boolean } = { touch: false }): Promise<Session | null> {
    const [rawMeta, rawSeen, rawTokens] = await Promise.all([
      this.kv.get(key.meta(id)),
      this.kv.get(key.seen(id)),
      this.kv.get(key.tokens(id)),
    ]);
    if (!rawMeta || !rawSeen || !rawTokens) {
      if (rawMeta || rawSeen || rawTokens) await this.revoke(id);
      return null;
    }
    const meta = JSON.parse(rawMeta) as StoredMeta;
    const tokens = JSON.parse(rawTokens) as StoredTokens;
    const seen = Number(rawSeen);
    const now = this.now();
    if (
      meta.v !== 1 ||
      !Number.isFinite(seen) ||
      now - seen >= this.idleTimeoutMs ||
      now - meta.created >= ABSOLUTE_TIMEOUT_MS[meta.kind]
    ) {
      await this.revoke(id, meta);
      return null;
    }
    if (!options.touch) return this.toSession(id, meta, seen, tokens.accessExp);
    const pxMs = this.ttl(meta, now);
    await this.kv.set(key.seen(id), String(now), { pxMs });
    await this.kv.pexpire(key.meta(id), pxMs);
    await this.kv.pexpire(key.tokens(id), pxMs);
    return this.toSession(id, meta, now, tokens.accessExp);
  }

  /** Decrypt the session's tokens (proxy and refresh only; never returned to the browser). */
  async tokens(id: string): Promise<{ tokens: TokenSet; accessExpiresAt: number } | null> {
    const raw = await this.kv.get(key.tokens(id));
    if (!raw) return null;
    const stored = JSON.parse(raw) as StoredTokens;
    const tokens = JSON.parse(unseal(this.tokenKey, stored.sealed, `tokens:${id}`)) as TokenSet;
    return { tokens, accessExpiresAt: stored.accessExp };
  }

  /** Replace tokens after a refresh (caller holds the refresh lock). */
  async saveTokens(session: Session, tokens: TokenSet, accessExpiresAt: number): Promise<void> {
    const pxMs = Math.max(1, Math.min(this.idleTimeoutMs, session.absoluteExpiresAt - this.now()));
    const stored: StoredTokens = { sealed: this.sealTokens(session.id, tokens), accessExp: accessExpiresAt };
    await this.kv.set(key.tokens(session.id), JSON.stringify(stored), { pxMs });
  }

  async setActiveTenant(session: Session, tenantId: string | null): Promise<void> {
    const raw = await this.kv.get(key.meta(session.id));
    if (!raw) return;
    const meta = JSON.parse(raw) as StoredMeta;
    meta.tenant = tenantId;
    await this.kv.set(key.meta(session.id), JSON.stringify(meta), {
      pxMs: this.ttl(meta, this.now()),
    });
  }

  async revoke(id: string, knownMeta?: StoredMeta): Promise<void> {
    let meta = knownMeta;
    if (!meta) {
      const raw = await this.kv.get(key.meta(id));
      meta = raw ? (JSON.parse(raw) as StoredMeta) : undefined;
    }
    await this.kv.del(key.meta(id), key.tokens(id), key.seen(id), key.lock(id));
    if (meta) {
      await this.kv.srem(key.index(meta.kind, meta.iss, meta.sub), id);
      await this.kv.srem(key.family(meta.family), id);
    }
  }

  /** Revoke every session in a refresh-token family (reuse detected; FR-IAM-004). */
  async revokeFamily(familyId: string): Promise<number> {
    const members = await this.kv.smembers(key.family(familyId));
    for (const id of members) await this.revoke(id);
    await this.kv.del(key.family(familyId));
    return members.length;
  }

  /** All live sessions of the same person and kind (for "your sessions"). */
  async listFor(owner: Pick<Session, "kind" | "issuer" | "subject">): Promise<Session[]> {
    const indexKey = key.index(owner.kind, owner.issuer, owner.subject);
    const sessions: Session[] = [];
    for (const id of await this.kv.smembers(indexKey)) {
      const session = await this.get(id);
      if (session) sessions.push(session);
      else await this.kv.srem(indexKey, id);
    }
    return sessions.sort((a, b) => b.lastSeenAt - a.lastSeenAt);
  }

  async revokeByHandle(owner: Session, handle: string): Promise<Session | null> {
    const target = (await this.listFor(owner)).find((session) => session.handle === handle);
    if (!target) return null;
    await this.revoke(target.id);
    return target;
  }

  /**
   * Record that a refresh token is being spent. Returns false if it was spent before:
   * refresh-token reuse, so the caller must revoke the family.
   */
  async spendRefreshToken(familyId: string, refreshToken: string): Promise<boolean> {
    const setKey = key.usedRefresh(familyId);
    const added = await this.kv.sadd(setKey, sha256(refreshToken));
    await this.kv.pexpire(setKey, ABSOLUTE_TIMEOUT_MS.staff);
    return added === 1;
  }

  /** The IdP did not rotate: the same refresh token stays current and may be spent again. */
  async unspendRefreshToken(familyId: string, refreshToken: string): Promise<void> {
    await this.kv.srem(key.usedRefresh(familyId), sha256(refreshToken));
  }
}
