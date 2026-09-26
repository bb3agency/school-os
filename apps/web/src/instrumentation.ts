/**
 * Runs once per Next.js server process (for the standalone server: before the first
 * request is handled). Refuses to run with an invalid BFF configuration (e.g.
 * SESSION_SECRET shorter than 32 bytes, plain http outside localhost, dev-only placeholder
 * secrets on https) instead of failing on the first sign-in. In production the process
 * exits, so the container health check fails loudly and the orchestrator restarts it.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { ConfigError, loadAuthConfig } = await import("@/server/config");
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
