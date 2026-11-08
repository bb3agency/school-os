import "server-only";
import { loadAuthConfig } from "@/server/config";
import type { PublicRuntime } from "./client";

/**
 * The runtime of the public pages (`/p/*`, `/d/*`, `/v/*`; FR-PUB-001; audit 2026-11-08 WS-04):
 * the BFF configuration (service-token key, API address, trusted proxy hops), `fetch` and the
 * clock. Nothing more: these pages hold no session and no rate-limit state of their own (the
 * API limits the public routes), so they never connect to Valkey and keep working while it is
 * down. Built once per server process; a configuration error is thrown as for the staff BFF
 * (the start-up check refuses to run with one in production).
 */

const GLOBAL_KEY = Symbol.for("schoolos.web.publicRuntime");
type Holder = { [GLOBAL_KEY]?: PublicRuntime | undefined };

export async function getPublicRuntime(): Promise<PublicRuntime> {
  const holder = globalThis as Holder;
  let runtime = holder[GLOBAL_KEY];
  if (!runtime) {
    runtime = {
      config: loadAuthConfig(),
      apiFetch: (input, init) => fetch(input, init),
      now: Date.now,
    };
    holder[GLOBAL_KEY] = runtime;
  }
  return runtime;
}

/** Tests only: install (or clear) the process-wide public runtime. */
export function setPublicRuntimeForTesting(runtime: PublicRuntime | null): void {
  (globalThis as Holder)[GLOBAL_KEY] = runtime ?? undefined;
}
