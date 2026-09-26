import "server-only";

/**
 * The few Valkey commands the session store needs. Production uses `RedisKeyValue`
 * (src/server/session/redis.ts); tests use `MemoryKeyValue`, which has the same semantics.
 */
export interface KeyValue {
  get(key: string): Promise<string | null>;
  /** SET key value [PX ms] [NX]; returns false when NX blocked the write. */
  set(key: string, value: string, options?: { pxMs?: number; nx?: boolean }): Promise<boolean>;
  del(...keys: string[]): Promise<void>;
  /** Delete only if the current value equals `value` (lock release). */
  delIfEquals(key: string, value: string): Promise<boolean>;
  pexpire(key: string, ms: number): Promise<void>;
  /** SADD; returns the number of members actually added (0 = already present). */
  sadd(key: string, member: string): Promise<number>;
  srem(key: string, member: string): Promise<void>;
  smembers(key: string): Promise<string[]>;
}

type Entry = { value: string | Set<string>; expiresAt: number | null };

/** In-memory KeyValue with TTLs driven by an injectable clock (tests, never production). */
export class MemoryKeyValue implements KeyValue {
  private readonly data = new Map<string, Entry>();

  constructor(private readonly now: () => number = Date.now) {}

  private live(key: string): Entry | undefined {
    const entry = this.data.get(key);
    if (entry && entry.expiresAt !== null && entry.expiresAt <= this.now()) {
      this.data.delete(key);
      return undefined;
    }
    return entry;
  }

  /** Raw dump for tests that assert what is (not) stored. */
  dump(): Record<string, string | string[]> {
    const out: Record<string, string | string[]> = {};
    for (const key of this.data.keys()) {
      const entry = this.live(key);
      if (entry) out[key] = typeof entry.value === "string" ? entry.value : [...entry.value];
    }
    return out;
  }

  async get(key: string): Promise<string | null> {
    const entry = this.live(key);
    return entry && typeof entry.value === "string" ? entry.value : null;
  }

  async set(key: string, value: string, options: { pxMs?: number; nx?: boolean } = {}) {
    if (options.nx && this.live(key)) return false;
    this.data.set(key, {
      value,
      expiresAt: options.pxMs !== undefined ? this.now() + options.pxMs : null,
    });
    return true;
  }

  async del(...keys: string[]): Promise<void> {
    for (const key of keys) this.data.delete(key);
  }

  async delIfEquals(key: string, value: string): Promise<boolean> {
    const entry = this.live(key);
    if (entry?.value !== value) return false;
    this.data.delete(key);
    return true;
  }

  async pexpire(key: string, ms: number): Promise<void> {
    const entry = this.live(key);
    if (entry) entry.expiresAt = this.now() + ms;
  }

  async sadd(key: string, member: string): Promise<number> {
    let entry = this.live(key);
    if (!entry) {
      entry = { value: new Set<string>(), expiresAt: null };
      this.data.set(key, entry);
    }
    if (typeof entry.value === "string") throw new Error("WRONGTYPE");
    if (entry.value.has(member)) return 0;
    entry.value.add(member);
    return 1;
  }

  async srem(key: string, member: string): Promise<void> {
    const entry = this.live(key);
    if (entry && typeof entry.value !== "string") {
      entry.value.delete(member);
      if (entry.value.size === 0) this.data.delete(key);
    }
  }

  async smembers(key: string): Promise<string[]> {
    const entry = this.live(key);
    return entry && typeof entry.value !== "string" ? [...entry.value] : [];
  }
}
