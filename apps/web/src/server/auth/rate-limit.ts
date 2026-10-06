import "server-only";
import { createHmac } from "node:crypto";
import { isIP } from "node:net";
import { logEvent } from "@/server/log";
import { type KeyValue, MemoryKeyValue } from "@/server/session/kv";

/**
 * Per-IP limits and failure backoff on the BFF's own sign-in routes (audit 2026-10-05 P2-07;
 * OWASP API4:2023; ASVS 2.2.1). The API never sees sign-in starts, callbacks or step-up
 * starts, so the BFF limits them itself; on the shared tier the WAF rule `rate-limit-auth`
 * (same budget) is the coarse first line, on dedicated hosts this is the only one.
 *
 * - Fixed window per client IP for every start (sign-in, step-up, support) and callback.
 * - Failed callbacks (state/nonce/PKCE/token refusals, IdP errors, MFA missing, step-up
 *   refusals) are counted per IP; after `free` failures the next attempt waits with an
 *   exponential delay (soft lockout, no account lockout). A successful sign-in clears it.
 * - Valkey unreachable: a per-process in-memory limiter takes over (fail closed).
 * - Logs carry `ip_hash` (the same keyed hash as the API, app/core/ratelimit.py), never the
 *   address. Valkey keys hold the hash only.
 */
export const AUTH_RATE_LIMITS = {
  /** Starts and callbacks per client IP; equals the WAF `waf_auth_rate_limit_per_5min`. */
  perIp: { limit: 300, windowS: 300 },
  /** Failed sign-in callbacks per client IP before the delay starts, and its growth. */
  failures: { free: 5, baseDelayS: 1, maxDelayS: 900, resetAfterS: 3600 },
} as const;

const PREFIX = "sos:rl:v1:bff";

/**
 * The client address behind `trustedHops` proxies (ALB or Caddy append the peer they saw to
 * X-Forwarded-For, so the entry `trustedHops` from the right is the one they vouch for; anything
 * further left came from the client). `unknown` without a usable entry.
 */
export function clientIp(headers: Headers, trustedHops: number): string {
  if (trustedHops <= 0) return "unknown";
  const hops = (headers.get("x-forwarded-for") ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const candidate = hops[hops.length - trustedHops];
  return candidate && isIP(candidate) ? candidate : "unknown";
}

/** HMAC-SHA256 of the address, 16 hex characters; same derivation as the API. */
export function ipHash(serviceTokenKey: Uint8Array, ip: string): string {
  const derived = createHmac("sha256", serviceTokenKey).update("sos-rate-limit-ip-hash").digest();
  return createHmac("sha256", derived).update(`sos-ip-hash-v1|${ip}`).digest("hex").slice(0, 16);
}

export class AuthLimiter {
  private readonly fallback: MemoryKeyValue;

  constructor(
    private readonly kv: KeyValue,
    private readonly now: () => number = Date.now,
  ) {
    this.fallback = new MemoryKeyValue(now);
  }

  private async use<T>(work: (kv: KeyValue) => Promise<T>): Promise<T> {
    try {
      return await work(this.kv);
    } catch {
      logEvent("rate_limit_unavailable");
      return work(this.fallback);
    }
  }

  /** Seconds to wait before this IP may start or finish a sign-in (0 = go ahead). */
  async check(hash: string): Promise<number> {
    const until = Number((await this.use((kv) => kv.get(`${PREFIX}:fail:${hash}:block`))) ?? 0);
    const now = this.now();
    if (until > now) return Math.max(1, Math.ceil((until - now) / 1000));
    const { limit, windowS } = AUTH_RATE_LIMITS.perIp;
    const { count, ttlMs } = await this.use((kv) =>
      kv.incr(`${PREFIX}:auth:${hash}`, windowS * 1000),
    );
    if (count <= limit) return 0;
    return Math.max(1, Math.ceil((ttlMs > 0 ? ttlMs : windowS * 1000) / 1000));
  }

  /** Count one failed sign-in from this IP; returns the delay now imposed in seconds. */
  async fail(hash: string): Promise<number> {
    const { free, baseDelayS, maxDelayS, resetAfterS } = AUTH_RATE_LIMITS.failures;
    const { count } = await this.use((kv) =>
      kv.incr(`${PREFIX}:fail:${hash}:n`, resetAfterS * 1000),
    );
    if (count <= free) return 0;
    const delayS = Math.min(baseDelayS * 2 ** Math.min(count - free - 1, 30), maxDelayS);
    const until = String(this.now() + delayS * 1000);
    await this.use((kv) => kv.set(`${PREFIX}:fail:${hash}:block`, until, { pxMs: delayS * 1000 }));
    return delayS;
  }

  /** A successful sign-in from this IP clears its failure count. */
  async clear(hash: string): Promise<void> {
    await this.use((kv) => kv.del(`${PREFIX}:fail:${hash}:n`, `${PREFIX}:fail:${hash}:block`));
  }
}
