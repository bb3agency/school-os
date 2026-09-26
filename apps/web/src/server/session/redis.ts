import "server-only";
import { createClient } from "@redis/client";
import { logEvent } from "@/server/log";
import type { KeyValue } from "./kv";

/** Compare-and-delete for lock release: only the holder's token removes the lock. */
const DEL_IF_EQUALS = `if redis.call("GET", KEYS[1]) == ARGV[1] then return redis.call("DEL", KEYS[1]) else return 0 end`;

const CONNECT_TIMEOUT_MS = 3_000;

function createValkeyClient(url: string) {
  return createClient({
    url,
    // Fail fast while disconnected instead of queueing (a request must not hang).
    disableOfflineQueue: true,
    socket: {
      connectTimeout: 2_000,
      reconnectStrategy: (retries: number) => Math.min(100 * 2 ** retries, 3_000),
    },
  });
}

type Client = ReturnType<typeof createValkeyClient>;

/** Valkey-backed KeyValue (node-redis client; Valkey speaks the Redis protocol, ADR-0014). */
export class RedisKeyValue implements KeyValue {
  constructor(private readonly client: Client) {}

  async get(key: string): Promise<string | null> {
    const value = await this.client.get(key);
    return value === null ? null : String(value);
  }

  async set(key: string, value: string, options: { pxMs?: number; nx?: boolean } = {}) {
    const result = await this.client.set(key, value, {
      ...(options.pxMs !== undefined
        ? { expiration: { type: "PX" as const, value: Math.max(1, Math.ceil(options.pxMs)) } }
        : {}),
      ...(options.nx ? { condition: "NX" as const } : {}),
    });
    return result !== null;
  }

  async del(...keys: string[]): Promise<void> {
    if (keys.length > 0) await this.client.del(keys);
  }

  async delIfEquals(key: string, value: string): Promise<boolean> {
    const result = await this.client.eval(DEL_IF_EQUALS, { keys: [key], arguments: [value] });
    return Number(result) === 1;
  }

  async pexpire(key: string, ms: number): Promise<void> {
    await this.client.pExpire(key, Math.max(1, Math.ceil(ms)));
  }

  async sadd(key: string, member: string): Promise<number> {
    return Number(await this.client.sAdd(key, member));
  }

  async srem(key: string, member: string): Promise<void> {
    await this.client.sRem(key, member);
  }

  async smembers(key: string): Promise<string[]> {
    return (await this.client.sMembers(key)).map(String);
  }
}

/** Connect to Valkey. Errors are logged by event name only (no URL, no credentials). */
export async function connectRedis(url: string): Promise<RedisKeyValue> {
  const client = createValkeyClient(url);
  client.on("error", () => logEvent("valkey_error"));
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("valkey connect timeout")), CONNECT_TIMEOUT_MS);
  });
  try {
    // connect() keeps retrying while Valkey is down; give up after a few seconds so the
    // caller answers 503 and the next request tries again.
    await Promise.race([client.connect(), timeout]);
  } catch (error) {
    client.destroy();
    throw error;
  } finally {
    clearTimeout(timer);
  }
  return new RedisKeyValue(client);
}
