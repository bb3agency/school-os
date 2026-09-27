/**
 * Node.js-only part of the startup check (imported by `instrumentation.ts` only when
 * `NEXT_RUNTIME === "nodejs"`, so the Edge build never contains `process.exit`).
 *
 * Refuses to run with an invalid BFF configuration (e.g. SESSION_SECRET shorter than 32
 * bytes, plain http outside localhost, dev-only placeholder secrets on https) instead of
 * failing on the first sign-in. In production the process exits, so the container health
 * check fails loudly and the orchestrator restarts it.
 */
import { ConfigError, loadAuthConfig } from "@/server/config";

export function checkServerConfig(): void {
  try {
    loadAuthConfig();
  } catch (error) {
    if (process.env.NODE_ENV === "production" && error instanceof ConfigError) {
      // Variable names only, never values.
      console.error(
        JSON.stringify({ level: "error", event: "bff_config_invalid", problems: error.problems }),
      );
      process.exit(1);
      return;
    }
    throw error;
  }
}
