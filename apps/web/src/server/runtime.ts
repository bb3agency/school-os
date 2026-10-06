import "server-only";
import { TransactionCodec } from "@/server/auth/transaction";
import { createOidcClient, type CustomFetch, type OidcClient } from "@/server/auth/oidc";
import { TokenRefresher } from "@/server/auth/refresh";
import { AuthLimiter } from "@/server/auth/rate-limit";
import { loadAuthConfig, type AuthConfig, type SessionKind } from "@/server/config";
import type { KeyValue } from "@/server/session/kv";
import { connectRedis } from "@/server/session/redis";
import { SessionStore } from "@/server/session/store";

/**
 * Everything the BFF route handlers need, built once per server process. Tests build
 * their own with `createAuthRuntime` (in-memory key-value store, fake IdP and API).
 */
export interface AuthRuntime {
  config: AuthConfig;
  store: SessionStore;
  oidc: Record<SessionKind, OidcClient>;
  refresher: TokenRefresher;
  transactions: TransactionCodec;
  /** Per-IP limits and failure backoff on the sign-in routes (P2-07). */
  authLimiter: AuthLimiter;
  /** fetch used for calls to the API (API_INTERNAL_URL). */
  apiFetch: typeof fetch;
  now: () => number;
}

export interface RuntimeDependencies {
  kv: KeyValue;
  oidcFetch?: CustomFetch;
  oidc?: Partial<Record<SessionKind, OidcClient>>;
  apiFetch?: typeof fetch;
  now?: () => number;
  refreshPollMs?: number;
}

export function createAuthRuntime(config: AuthConfig, deps: RuntimeDependencies): AuthRuntime {
  const now = deps.now ?? Date.now;
  const store = new SessionStore(deps.kv, config.sessionSecret, { now });
  const oidcOptions = { now, ...(deps.oidcFetch ? { fetch: deps.oidcFetch } : {}) };
  const oidc: Record<SessionKind, OidcClient> = {
    staff: deps.oidc?.staff ?? createOidcClient(config.staff, oidcOptions),
    operator: deps.oidc?.operator ?? createOidcClient(config.operator, oidcOptions),
    // Lazy (discovery on first use); never used while config.supportEnabled is false.
    support: deps.oidc?.support ?? createOidcClient(config.support, oidcOptions),
  };
  return {
    config,
    store,
    oidc,
    refresher: new TokenRefresher({
      store,
      oidc,
      now,
      ...(deps.refreshPollMs !== undefined ? { pollMs: deps.refreshPollMs } : {}),
    }),
    transactions: new TransactionCodec(config.sessionSecret, now),
    authLimiter: new AuthLimiter(deps.kv, now),
    apiFetch: deps.apiFetch ?? fetch,
    now,
  };
}

const GLOBAL_KEY = Symbol.for("schoolos.web.authRuntime");
type Holder = { [GLOBAL_KEY]?: Promise<AuthRuntime> | undefined };

/** Process-wide runtime from the environment (connects to Valkey on first use). */
export function getAuthRuntime(): Promise<AuthRuntime> {
  const holder = globalThis as Holder;
  let runtime = holder[GLOBAL_KEY];
  if (!runtime) {
    runtime = (async () => {
      const config = loadAuthConfig();
      const kv = await connectRedis(config.redisUrl);
      return createAuthRuntime(config, { kv });
    })();
    runtime.catch(() => {
      if (holder[GLOBAL_KEY] === runtime) holder[GLOBAL_KEY] = undefined;
    });
    holder[GLOBAL_KEY] = runtime;
  }
  return runtime;
}

/** Tests only: install (or clear) the process-wide runtime. */
export function setAuthRuntimeForTesting(runtime: AuthRuntime | null): void {
  (globalThis as Holder)[GLOBAL_KEY] = runtime ? Promise.resolve(runtime) : undefined;
}
